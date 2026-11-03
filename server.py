"""HomeDash: self-hosted links and read-only service status."""
from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import ssl
from functools import lru_cache
import httpx
from cryptography.fernet import Fernet
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware
from home_security import LoginGuard, parse_networks

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('HOMEDASH_DATA', './data'))
DATA.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get('HOMEDASH_PUBLIC_URL', 'http://127.0.0.1:8790').rstrip('/')
SECURE = BASE.startswith('https://')
def normalize_issuer(value):
    value = value.strip().rstrip('/')
    suffix = '/.well-known/openid-configuration'
    if value.endswith(suffix):
        value = value[:-len(suffix)]
    return value

ISSUER = normalize_issuer(os.environ.get('HOMEDASH_OIDC_ISSUER', ''))
CLIENT = os.environ.get('HOMEDASH_OIDC_CLIENT_ID', '')
SECRET_FILE = os.environ.get('HOMEDASH_OIDC_CLIENT_SECRET_FILE', '')
if any((ISSUER, CLIENT, SECRET_FILE)) and not all((ISSUER, CLIENT, SECRET_FILE)):
    raise RuntimeError('Set all three OIDC issuer, client ID and client secret file settings')
if ISSUER and not ISSUER.startswith('https://'):
    raise RuntimeError('OIDC issuer must use HTTPS')

@contextmanager
def db():
    c = sqlite3.connect(DATA / 'homedash.db', timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        with c:
            yield c
    finally:
        c.close()

with db() as c:
    c.executescript('''
    CREATE TABLE IF NOT EXISTS trust_certificates (fingerprint TEXT PRIMARY KEY, name TEXT NOT NULL, pem TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT, admin INTEGER NOT NULL DEFAULT 0, oidc_sub TEXT UNIQUE, version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS oidc_links (subject TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, kind TEXT NOT NULL, base_url TEXT NOT NULL, config TEXT NOT NULL, secret_enc TEXT NOT NULL, ca_pem TEXT NOT NULL DEFAULT '', interval INTEGER NOT NULL DEFAULT 60, revision INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, group_id TEXT REFERENCES groups(id) ON DELETE SET NULL, name TEXT NOT NULL, url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT 'grid', connection_id TEXT REFERENCES connections(id) ON DELETE SET NULL, position INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS snapshots (connection_id TEXT PRIMARY KEY REFERENCES connections(id) ON DELETE CASCADE, payload TEXT NOT NULL);
    ''')
    c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('session_secret', secrets.token_urlsafe(48)))
    SESSION_SECRET = c.execute('SELECT value FROM settings WHERE key=?', ('session_secret',)).fetchone()[0]
app = FastAPI(title='HomeDash', docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie='homedash', same_site='lax', https_only=SECURE, max_age=43200)
oauth = None
if ISSUER:
    oauth = OAuth()
    options = {'scope': 'openid profile email', 'code_challenge_method': 'S256'}
    if os.environ.get('HOMEDASH_OIDC_CA_FILE'):
        options['verify'] = os.environ['HOMEDASH_OIDC_CA_FILE']
    oauth.register('oidc', server_metadata_url=ISSUER.rstrip('/')+'/.well-known/openid-configuration', client_id=CLIENT, client_secret=Path(SECRET_FILE).read_text().strip(), client_kwargs=options)

# Password sign-ins are limited per source and account, and the real client address comes from a trusted proxy only.
# HOMEDASH_TRUSTED_PROXIES lists the reverse proxy's address or network, for example 172.28.0.1 or 10.0.0.0/8.
guard = LoginGuard(parse_networks(os.environ.get('HOMEDASH_TRUSTED_PROXIES', '')))

@app.middleware('http')
async def boundary(request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('origin', BASE) != BASE:
            return Response('Invalid request origin', status_code=403)
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            return Response('JSON required', status_code=415)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path == '/' or request.url.path.startswith(('/static/', '/api/', '/download/', '/auth/', '/login/')):
        response.headers['Cache-Control'] = 'no-store'
    return response

def password_hash(password):
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64*1024*1024)
    return salt.hex()+':'+result.hex()

def password_ok(password, encoded):
    if not encoded:
        # Keep unknown and SSO-only user work comparable to local users.
        encoded = '00'*16+':'+'00'*32
    salt, expected = encoded.split(':')
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1, maxmem=64*1024*1024).hex()
    return hmac.compare_digest(actual, expected)

def current(request: Request):
    with db() as c:
        user = c.execute('SELECT * FROM users WHERE id=?', (request.session.get('uid', ''),)).fetchone()
    if not user or request.session.get('version') != user['version']:
        raise HTTPException(401, 'Please log in')
    return dict(user)

def admin(user=Depends(current)):
    if not user['admin']:
        raise HTTPException(403, 'Administrator access required')
    return user

def sign_in(request, user):
    request.session.clear()
    request.session.update(uid=user['id'], version=user['version'])

def public_user(user):
    return {'id':user['id'], 'username':user['username'], 'admin':bool(user['admin']), 'local':bool(user['password'])}


def saved_options(key):
    with db() as c:
        row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def store_options(key, value):
    with db() as c:
        c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))


def certificate_pem(value):
    if value:
        if 'PRIVATE KEY' in value:
            raise HTTPException(400, 'Provide public CA certificates, never private keys')
        try:
            ssl.create_default_context().load_verify_locations(cadata=value)
        except (ssl.SSLError, ValueError):
            raise HTTPException(400, 'Invalid PEM certificate bundle')
    return value


def trusted_pem():
    with db() as c:
        return '\n'.join(r[0] for r in c.execute('SELECT pem FROM trust_certificates ORDER BY fingerprint'))


def certificate_info(row):
    cert=x509.load_pem_x509_certificate(row['pem'].encode())
    return {'fingerprint':row['fingerprint'],'name':row['name'],
            'subject':cert.subject.rfc4514_string(),'issuer':cert.issuer.rfc4514_string(),
            'expires':cert.not_valid_after_utc.isoformat()}


def trust_changed(c):
    # Invalidate in-flight results too, so checks cannot retain an obsolete trust decision.
    c.execute('UPDATE connections SET revision=revision+1')
    c.execute('DELETE FROM snapshots')
    configured_oauth.cache_clear()


def secret_cipher():
    path = DATA / 'secrets.key'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as file:
            file.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def oidc_options():
    saved = saved_options('oidc')
    if saved is not None:
        return saved
    path = os.environ.get('HOMEDASH_OIDC_CA_FILE')
    return {'enabled':bool(oauth), 'issuer':ISSUER, 'client_id':CLIENT, 'secret_enc':'',
            'allowed_users':os.environ.get('HOMEDASH_OIDC_ALLOWED_USERS',''), 'ca_pem':Path(path).read_text() if path else ''}


@lru_cache(maxsize=4)
def configured_oauth(serialized):
    value = json.loads(serialized)
    registry = OAuth()
    context = ssl.create_default_context()
    if value['ca_pem']:
        context.load_verify_locations(cadata=value['ca_pem'])
    registry.register('oidc', server_metadata_url=value['issuer'].rstrip('/')+'/.well-known/openid-configuration',
        client_id=value['client_id'], client_secret=secret_cipher().decrypt(value['secret_enc'].encode()).decode() if value['secret_enc'] else Path(SECRET_FILE).read_text().strip(),
        client_kwargs={'scope':'openid profile email','code_challenge_method':'S256','verify':context})
    return registry


def oidc_runtime():
    value=oidc_options()
    if not value['enabled']:
        return None,value['issuer'],value['allowed_users']
    value={**value,'ca_pem':'\n'.join(filter(None,(value['ca_pem'],trusted_pem())))}
    return configured_oauth(json.dumps(value,sort_keys=True)),value['issuer'],value['allowed_users']


def ui_options():
    return saved_options('ui') or {'theme':'auto'}

class UISettingsIn(BaseModel):
    theme: str = Field(default='auto',pattern='^(auto|light|dark)$')

@app.put('/api/settings/ui')
def set_ui(body:UISettingsIn,user=Depends(admin)):
    store_options('ui',body.model_dump())
    return ui_options()

class OIDCSettingsIn(BaseModel):
    enabled: bool = False
    issuer: str = Field(default='', max_length=2048)
    client_id: str = Field(default='', max_length=256)
    client_secret: str = Field(default='', max_length=8192)
    allowed_users: str = Field(default='', max_length=4096)
    ca_pem: str = Field(default='', max_length=100000)


@app.get('/api/settings')
def server_settings(user=Depends(admin)):
    oidc = oidc_options()
    return {'ui':ui_options(), 'oidc':{k:v for k,v in oidc.items() if k != 'secret_enc'},
            'oidc_secret_configured':bool(oidc['secret_enc'] or (saved_options('oidc') is None and SECRET_FILE)),
            'callback_url':BASE+'/auth/callback'}


@app.put('/api/settings/oidc')
def set_oidc(body: OIDCSettingsIn, user=Depends(admin)):
    from urllib.parse import urlsplit
    value = body.model_dump(exclude={'client_secret'})
    value['issuer'] = normalize_issuer(value['issuer'])
    certificate_pem(value['ca_pem'])
    old = oidc_options()
    if 'ca_pem' not in body.model_fields_set:
        value['ca_pem']=old['ca_pem']
    encrypted = old['secret_enc']
    if body.client_secret:
        encrypted = secret_cipher().encrypt(body.client_secret.encode()).decode()
    elif not encrypted and saved_options('oidc') is None and SECRET_FILE:
        encrypted = secret_cipher().encrypt(Path(SECRET_FILE).read_text().strip().encode()).decode()
    if body.enabled:
        parsed = urlsplit(value['issuer'])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(400, 'Issuer must be an HTTPS URL')
        if not value['client_id'].strip() or not encrypted:
            raise HTTPException(400, 'Client ID and client secret are required')
    value['secret_enc'] = encrypted
    store_options('oidc',value)
    configured_oauth.cache_clear()
    return {'ok':True, 'settings':server_settings(user)}


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    @field_validator('username')
    @classmethod
    def username_valid(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+', value):
            raise ValueError('Use letters, numbers, dots, @, underscores or hyphens')
        return value

class NewUser(Login):
    admin: bool = False

class PasswordChange(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(min_length=12, max_length=1024)

@app.get('/api/state')
def state(request: Request):
    try:
        user = current(request)
    except HTTPException:
        user = None
    with db() as c:
        setup = c.execute('SELECT count(*) FROM users').fetchone()[0] == 0
    return {'logged_in':bool(user), 'user':public_user(user) if user else None, 'setup_needed':setup, 'sso':bool(oidc_options()['enabled']), 'ui':ui_options()}

@app.post('/api/setup')
def setup(body: Login, request: Request):
    if len(body.password) < 12:
        raise HTTPException(400, 'Use at least 12 characters')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM users').fetchone():
            raise HTTPException(409, 'Setup is already complete')
        uid = secrets.token_hex(16)
        c.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', (uid, body.username, password_hash(body.password), 1, None, 1))
        user = dict(c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
    sign_in(request, user)
    return public_user(user)

@app.post('/api/login')
def login(body: Login, request: Request):
    guard.check(request, body.username)
    with db() as c:
        user = c.execute('SELECT * FROM users WHERE username=?', (body.username,)).fetchone()
    if not password_ok(body.password, user['password'] if user else None):
        guard.fail(request, body.username)
        raise HTTPException(401, 'Incorrect username or password')
    guard.success(request, body.username)
    sign_in(request, user)
    return public_user(user)

@app.post('/api/logout')
def logout(request: Request):
    request.session.clear()
    return {'ok':True}

@app.post('/api/password')
def change_password(body: PasswordChange, request: Request, user=Depends(current)):
    if not user['password'] or not password_ok(body.current, user['password']):
        raise HTTPException(403, 'Current password is incorrect')
    with db() as c:
        c.execute('UPDATE users SET password=?,version=version+1 WHERE id=?', (password_hash(body.new), user['id']))
    user['version'] += 1
    sign_in(request, user)
    return {'ok':True}

@app.get('/api/users')
def users(user=Depends(admin)):
    with db() as c:
        return [public_user(dict(row)) for row in c.execute('SELECT * FROM users ORDER BY username')]

@app.post('/api/users')
def add_user(body: NewUser, user=Depends(admin)):
    if len(body.password) < 12:
        raise HTTPException(400, 'Use at least 12 characters')
    try:
        with db() as c:
            c.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', (secrets.token_hex(16),body.username,password_hash(body.password),int(body.admin),None,1))
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Username already exists')
    return {'ok':True}

@app.delete('/api/users/{uid}')
def delete_user(uid: str, user=Depends(admin)):
    if uid == user['id']:
        raise HTTPException(400, 'You cannot remove your own account')
    with db() as c:
        c.execute('DELETE FROM users WHERE id=?', (uid,))
    return {'ok':True}

def linked_oidc_user(subject, username):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        linked = c.execute('SELECT users.* FROM oidc_links JOIN users ON users.id=oidc_links.user_id WHERE subject=?', (subject,)).fetchone()
        if linked:
            return dict(linked)
        bound = c.execute('SELECT * FROM users WHERE oidc_sub=?', (subject,)).fetchone()
        if bound and bound['password']:
            return dict(bound)
        local = c.execute('SELECT * FROM users WHERE username=? AND password IS NOT NULL', (username,)).fetchone() if isinstance(username, str) else None
        if local:
            previous = c.execute('SELECT subject FROM oidc_links WHERE user_id=?', (local['id'],)).fetchone()
            if (local['oidc_sub'] and local['oidc_sub'] != subject) or (previous and previous['subject'] != subject):
                raise HTTPException(403, 'This local account is already linked to another SSO identity')
            # A login mapping only: existing accounts, catalogs, passwords and roles stay intact.
            c.execute('INSERT INTO oidc_links VALUES (?,?)', (subject,local['id']))
            return dict(local)
        if bound:
            return dict(bound)
        uid = secrets.token_hex(16)
        label = str(username or 'sso')[:40]+'~'+uid[:8]
        c.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', (uid,label,None,0,subject,1))
        return dict(c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())


def oidc_failure(error):
    # No provider response, credentials, authorization codes or tokens go into the URL.
    code = 'tls' if 'CERTIFICATE_VERIFY_FAILED' in str(error) else 'failed'
    return RedirectResponse('/?sso_error='+code)

@app.get('/login/oidc')
async def oidc_login(request: Request):
    provider, issuer, allowed = oidc_runtime()
    if not provider:
        raise HTTPException(404, 'OIDC is not configured')
    with db() as c:
        if not c.execute('SELECT 1 FROM users WHERE admin=1').fetchone():
            raise HTTPException(409, 'Create the local administrator first')
    try:
        return await provider.oidc.authorize_redirect(request, BASE+'/auth/callback')
    except Exception as error:
        return oidc_failure(error)

@app.get('/auth/callback')
async def oidc_callback(request: Request):
    provider, issuer, allowed = oidc_runtime()
    if not provider:
        raise HTTPException(404)
    try:
        token = await provider.oidc.authorize_access_token(request)
        info = token.get('userinfo') or {}
        subject = info.get('sub')
        if not subject:
            raise ValueError('Missing subject')
        subject = issuer + '|' + subject
        allow = [s.strip() for s in allowed.split(',') if s.strip()]
        if allow and info.get('preferred_username') not in allow and info.get('email') not in allow:
            raise HTTPException(403, 'This identity is not allowed')
        user = linked_oidc_user(subject, info.get('preferred_username'))
        sign_in(request, user)
        return RedirectResponse('/')
    except HTTPException:
        raise
    except Exception as error:
        return oidc_failure(error)


# Dashboard collections and credentials belong to the signed-in user.
import asyncio
from contextlib import asynccontextmanager, suppress
from connectors import REGISTRY, manifest, validate_url, load_json_connectors, validate_ping_target, client as connector_client
CONNECTORS_DIR=os.environ.get('HOMEDASH_CONNECTORS_DIR', DATA/'connectors')

def refresh_connectors():
    try:
        return load_json_connectors(CONNECTORS_DIR)
    except (ValueError,OSError):
        # Invalid edits never replace the last working set or interrupt polling.
        import logging
        logging.getLogger('homedash').warning('Service definitions could not be reloaded; keeping the last working set')
        return False

refresh_connectors()

class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    @field_validator('name')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Name is required')
        return value.strip()

LUCIDE_ICONS={item['name'] for item in json.loads((ROOT/'static/lucide/catalog.json').read_text())['icons']}
LEGACY_ICONS={'grid','server','home','network','storage','container','terminal','book','media','globe'}

class CardIn(GroupIn):
    url: str = Field(max_length=2048)
    description: str = Field(default='', max_length=1000)
    icon: str = Field(default='grid', max_length=100)
    group_id: str | None = None
    connection_id: str | None = None
    @field_validator('url')
    @classmethod
    def url_valid(cls, value):
        # Fragments are useful on browser links, unlike polling endpoints.
        from urllib.parse import urldefrag
        validate_url(urldefrag(value)[0])
        return value.strip()
    @field_validator('icon')
    @classmethod
    def icon_valid(cls, value):
        if value not in LUCIDE_ICONS and value not in LEGACY_ICONS:
            raise ValueError('Choose a supported icon')
        return value

class ConnectionIn(GroupIn):
    kind: str
    base_url: str = Field(max_length=2048)
    config: dict[str,str] = Field(default_factory=dict, max_length=8)
    secret: str = Field(default='', max_length=8192)
    ca_pem: str = Field(default='', max_length=100000)
    interval: int = Field(default=60, ge=15, le=3600)
    @field_validator('config')
    @classmethod
    def config_bounded(cls, value):
        if any(len(key)>80 or len(text)>2048 for key,text in value.items()):
            raise ValueError('Connector configuration is too long')
        return value
    @field_validator('base_url')
    @classmethod
    def url_valid(cls, value, info):
        if info.data.get('kind')=='ping':
            return validate_ping_target(value)
        return validate_url(value).rstrip('/')
    @field_validator('kind')
    @classmethod
    def kind_valid(cls, value):
        if value not in REGISTRY:
            raise ValueError('Unknown connector')
        return value

class ServiceIn(CardIn):
    connection: ConnectionIn | None = None

class TrustUpload(GroupIn):
    certificate: str = Field(min_length=1,max_length=500000)
    encoding: str = Field(default='pem',pattern='^(pem|base64)$')

@app.get('/api/settings/trust')
def list_trust(user=Depends(admin)):
    with db() as c:
        return [certificate_info(row) for row in c.execute('SELECT * FROM trust_certificates ORDER BY name,fingerprint')]

@app.post('/api/settings/trust')
def add_trust(body:TrustUpload,user=Depends(admin)):
    try:
        if 'PRIVATE KEY' in body.certificate:
            raise ValueError('Private key')
        raw=base64.b64decode(body.certificate,validate=True) if body.encoding=='base64' else body.certificate.encode()
        if b'PRIVATE KEY' in raw:
            raise ValueError('Private key')
        certs=x509.load_pem_x509_certificates(raw) if b'-----BEGIN CERTIFICATE-----' in raw else [x509.load_der_x509_certificate(raw)]
        if not certs or len(certs)>32:
            raise ValueError('Bundle size')
        entries=[(cert.fingerprint(hashes.SHA256()).hex(),body.name.strip(),cert.public_bytes(serialization.Encoding.PEM).decode()) for cert in certs]
        if not body.name.strip():
            raise ValueError('Name required')
        for _,_,pem in entries:
            certificate_pem(pem)
    except (ValueError,TypeError):
        raise HTTPException(400,'Upload public PEM or DER certificates only. Private keys are not accepted.')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        existing={r[0] for r in c.execute('SELECT fingerprint FROM trust_certificates')}
        if len(existing|{r[0] for r in entries})>256:
            raise HTTPException(400,'Trust store is full (256 certificates)')
        added=0
        for entry in entries:
            added+=c.execute('INSERT OR IGNORE INTO trust_certificates VALUES (?,?,?)',entry).rowcount
        if added:
            trust_changed(c)
    return {'ok':True,'added':added,'certificates':list_trust(user)}

@app.delete('/api/settings/trust/{fingerprint}')
def remove_trust(fingerprint:str,user=Depends(admin)):
    with db() as c:
        if not c.execute('DELETE FROM trust_certificates WHERE fingerprint=?',(fingerprint,)).rowcount:
            raise HTTPException(404,'Certificate not found')
        trust_changed(c)
    return {'ok':True}

class OrderIn(BaseModel):
    ids: list[str] = Field(max_length=1000)


def owned(c, table, ident, user):
    # Table names come only from application constants.
    row=c.execute(f'SELECT * FROM {table} WHERE id=? AND owner=?',(ident,user['id'])).fetchone()
    if not row:
        raise HTTPException(404,'Item not found')
    return dict(row)


def public_connection(row):
    return {**{k:v for k,v in dict(row).items() if k not in ('secret_enc','owner','revision')},
            'config':json.loads(row['config']), 'secret_configured':bool(row['secret_enc'])}

@app.get('/api/plugins')
def plugins(user=Depends(current)):
    refresh_connectors()
    return manifest()

@app.get('/api/catalog')
def catalog(user=Depends(current)):
    with db() as c:
        return {'groups':[dict(r) for r in c.execute('SELECT id,name,position FROM groups WHERE owner=? ORDER BY position,id',(user['id'],))],
                'cards':[dict(r) for r in c.execute('SELECT id,group_id,name,url,description,icon,connection_id,position FROM cards WHERE owner=? ORDER BY position,id',(user['id'],))],
                'connections':[public_connection(r) for r in c.execute('SELECT * FROM connections WHERE owner=? ORDER BY name',(user['id'],))]}

@app.post('/api/groups')
def add_group(body:GroupIn,user=Depends(current)):
    ident=secrets.token_hex(12)
    with db() as c:
        position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM groups WHERE owner=?',(user['id'],)).fetchone()[0]
        c.execute('INSERT INTO groups VALUES (?,?,?,?)',(ident,user['id'],body.name,position))
    return {'id':ident}

@app.put('/api/groups/{ident}')
def edit_group(ident:str,body:GroupIn,user=Depends(current)):
    with db() as c:
        owned(c,'groups',ident,user)
        c.execute('UPDATE groups SET name=? WHERE id=?',(body.name,ident))
    return {'ok':True}

@app.delete('/api/groups/{ident}')
def delete_group(ident:str,user=Depends(current)):
    with db() as c:
        owned(c,'groups',ident,user)
        c.execute('DELETE FROM groups WHERE id=?',(ident,))
    return {'ok':True}


def card_references(c,body,user):
    if body.group_id:
        owned(c,'groups',body.group_id,user)
    if body.connection_id:
        owned(c,'connections',body.connection_id,user)

def inline_connection(c,body,user):
    if body.connection is None:
        return body.connection_id
    settings=body.connection
    old=owned(c,'connections',body.connection_id,user) if body.connection_id else None
    config,encrypted=connection_values(settings,old)
    # Editing a shared check from a card must not change other cards.
    shared=old and c.execute('SELECT count(*) FROM cards WHERE connection_id=?',(old['id'],)).fetchone()[0]>1
    if old and not shared:
        ident=old['id']
        values=(settings.name,settings.kind,settings.base_url,config,encrypted,connection_ca(settings,old),settings.interval)
        before=tuple(old[k] for k in ('name','kind','base_url','config','secret_enc','ca_pem','interval'))
        if values!=before:
            c.execute('UPDATE connections SET name=?,kind=?,base_url=?,config=?,secret_enc=?,ca_pem=?,interval=?,revision=revision+1 WHERE id=?',(*values,ident))
            c.execute('DELETE FROM snapshots WHERE connection_id=?',(ident,))
    else:
        ident=secrets.token_hex(12)
        c.execute('INSERT INTO connections VALUES (?,?,?,?,?,?,?,?,?,?)',(ident,user['id'],settings.name,settings.kind,settings.base_url,config,encrypted,connection_ca(settings,old),settings.interval,1))
    return ident

@app.post('/api/cards')
def add_card(body:ServiceIn,user=Depends(current)):
    ident=secrets.token_hex(12)
    with db() as c:
        card_references(c,body,user)
        connection_id=inline_connection(c,body,user)
        position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM cards WHERE owner=?',(user['id'],)).fetchone()[0]
        c.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?)',(ident,user['id'],body.group_id,body.name,body.url,body.description,body.icon,connection_id,position))
    return {'id':ident}

@app.put('/api/cards/{ident}')
def edit_card(ident:str,body:ServiceIn,user=Depends(current)):
    with db() as c:
        owned(c,'cards',ident,user)
        card_references(c,body,user)
        connection_id=inline_connection(c,body,user)
        c.execute('UPDATE cards SET group_id=?,name=?,url=?,description=?,icon=?,connection_id=? WHERE id=?',(body.group_id,body.name,body.url,body.description,body.icon,connection_id,ident))
    return {'ok':True}

@app.delete('/api/cards/{ident}')
def delete_card(ident:str,user=Depends(current)):
    with db() as c:
        owned(c,'cards',ident,user)
        c.execute('DELETE FROM cards WHERE id=?',(ident,))
    return {'ok':True}

@app.put('/api/order/{collection}')
def reorder(collection:str,body:OrderIn,user=Depends(current)):
    if collection not in ('cards','groups'):
        raise HTTPException(404)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        expected={r[0] for r in c.execute(f'SELECT id FROM {collection} WHERE owner=?',(user['id'],))}
        if len(set(body.ids))!=len(body.ids) or set(body.ids)!=expected:
            raise HTTPException(400,'Order must contain each of your items exactly once. Refresh and try again.')
        c.executemany(f'UPDATE {collection} SET position=? WHERE id=?',enumerate(body.ids))
    return {'ok':True}


def connection_ca(body,old=None):
    return old['ca_pem'] if old and 'ca_pem' not in body.model_fields_set else body.ca_pem

def connection_values(body,old=None):
    certificate_pem(body.ca_pem)
    plugin=REGISTRY[body.kind]
    try:
        config=plugin.validate(body.config)
    except ValueError as error:
        raise HTTPException(400,str(error))
    encrypted=old['secret_enc'] if old and old['kind']==body.kind else ''
    if body.secret:
        if '\r' in body.secret or '\n' in body.secret:
            raise HTTPException(400,'Token must be a single line')
        encrypted=secret_cipher().encrypt(body.secret.encode()).decode()
    if plugin.secret_label and not encrypted:
        raise HTTPException(400,'An API token is required')
    if plugin.secret_label and urlsplit(body.base_url).scheme!='https':
        raise HTTPException(400,'Use HTTPS for connections carrying API credentials')
    if not plugin.secret_label:
        encrypted=''
    return json.dumps(config),encrypted

@app.post('/api/connections')
def add_connection(body:ConnectionIn,user=Depends(current)):
    config,encrypted=connection_values(body)
    ident=secrets.token_hex(12)
    with db() as c:
        c.execute('INSERT INTO connections VALUES (?,?,?,?,?,?,?,?,?,?)',(ident,user['id'],body.name,body.kind,body.base_url,config,encrypted,body.ca_pem,body.interval,1))
    return {'id':ident}

@app.put('/api/connections/{ident}')
def edit_connection(ident:str,body:ConnectionIn,user=Depends(current)):
    with db() as c:
        old=owned(c,'connections',ident,user)
        config,encrypted=connection_values(body,old)
        c.execute('UPDATE connections SET name=?,kind=?,base_url=?,config=?,secret_enc=?,ca_pem=?,interval=?,revision=revision+1 WHERE id=?',(body.name,body.kind,body.base_url,config,encrypted,connection_ca(body,old),body.interval,ident))
        c.execute('DELETE FROM snapshots WHERE connection_id=?',(ident,))
    return {'ok':True}

@app.delete('/api/connections/{ident}')
def delete_connection(ident:str,user=Depends(current)):
    with db() as c:
        owned(c,'connections',ident,user)
        c.execute('DELETE FROM connections WHERE id=?',(ident,))
    return {'ok':True}

POLL_LOCKS={}
POLL_CAPACITY=asyncio.Semaphore(4)

async def poll_connection(row,force=False):
    ident=row['id']
    lock=POLL_LOCKS.setdefault(ident,asyncio.Lock())
    async with lock:
        with db() as c:
            latest=c.execute('SELECT * FROM connections WHERE id=?',(ident,)).fetchone()
            if not latest:
                return None
            row=dict(latest)
            saved=c.execute('SELECT payload FROM snapshots WHERE connection_id=?',(ident,)).fetchone()
        previous=json.loads(saved[0]) if saved else {}
        if not force and time.time()-previous.get('checked_at',0)<row['interval']:
            return previous
        async with POLL_CAPACITY:
            now=time.time()
            try:
                secret=secret_cipher().decrypt(row['secret_enc'].encode()).decode() if row['secret_enc'] else ''
                async with asyncio.timeout(20):
                    async with connector_client('\n'.join(filter(None,(trusted_pem(),row['ca_pem'])))) as http:
                        result=await REGISTRY[row['kind']].poll(http,row['base_url'],json.loads(row['config']),secret)
                payload={'status':result.status,'summary':result.summary,'metrics':result.metrics,'details':result.details,'checked_at':now,'last_success':now}
            except Exception as error:
                tls_error=False
                cause=error
                seen=set()
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    if isinstance(cause,ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in str(cause):
                        tls_error=True
                    cause=cause.__cause__ or cause.__context__
                # Do not return exception strings: URLs, provider responses and tokens may be sensitive.
                payload={'status':'unavailable','summary':('Certificate verification failed. Ask an administrator to upload the service CA certificate under Server settings → Trust.' if tls_error else ('Ping failed. Check the hostname, DNS and ICMP permissions.' if row['kind']=='ping' else 'Connection failed. Check the URL, token and API permissions.')),'error_code':'tls_untrusted' if tls_error else 'connection_failed',
                         'metrics':previous.get('metrics',[]),'details':previous.get('details',{}),'checked_at':now,'last_success':previous.get('last_success')}
        with db() as c:
            latest=c.execute('SELECT revision FROM connections WHERE id=?',(ident,)).fetchone()
            if latest and latest[0]==row['revision']:
                c.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?)',(ident,json.dumps(payload)))
                return payload
        return None

@app.get('/api/status')
def status(user=Depends(current)):
    now=time.time()
    with db() as c:
        rows=c.execute('SELECT connections.id,connections.interval,snapshots.payload FROM connections LEFT JOIN snapshots ON snapshots.connection_id=connections.id WHERE owner=?',(user['id'],)).fetchall()
    result={}
    for row in rows:
        value=json.loads(row['payload']) if row['payload'] else {'status':'pending','summary':'Waiting for first check','metrics':[],'checked_at':None,'last_success':None}
        value['stale']=bool(value['last_success'] and (value['status']=='unavailable' or now-value['last_success']>row['interval']*2))
        result[row['id']]=value
    return result

@app.get('/api/geocode')
async def geocode(q:str=Query(min_length=2,max_length=80),user=Depends(current)):
    from connectors.weather import search_cities
    try:
        async with connector_client(trusted_pem()) as http:
            return await search_cities(http,q.strip())
    except Exception:
        raise HTTPException(502,'City search failed. Enter coordinates manually.')

@app.post('/api/connections/{ident}/test')
async def test_connection(ident:str,user=Depends(current)):
    with db() as c:
        row=owned(c,'connections',ident,user)
    return await poll_connection(row)

async def poll_loop():
    while True:
        refresh_connectors()
        with db() as c:
            header=[k for k,p in REGISTRY.items() if getattr(p,'placement','')=='header']
            rows=[dict(row) for row in c.execute(f'SELECT * FROM connections WHERE EXISTS (SELECT 1 FROM cards WHERE cards.connection_id=connections.id) OR kind IN ({",".join("?"*len(header))})',header)]
        await asyncio.gather(*(poll_connection(row) for row in rows))
        active={row['id'] for row in rows}
        for ident in list(POLL_LOCKS):
            if ident not in active and not POLL_LOCKS[ident].locked():
                del POLL_LOCKS[ident]
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(application):
    task=asyncio.create_task(poll_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

app.router.lifespan_context=lifespan

@app.get('/healthz')
def health():
    return {'ok':True}

@app.get('/')
def index():
    return HTMLResponse((ROOT/'static/index.html').read_text().replace('__HOME_THEME__',ui_options()['theme']))

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
