import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

TEMP=tempfile.TemporaryDirectory()
os.environ['HOMEDASH_DATA']=TEMP.name
os.environ['HOMEDASH_PUBLIC_URL']='http://testserver'
for key in ('ISSUER','CLIENT_ID','CLIENT_SECRET_FILE'):
    os.environ.pop('HOMEDASH_OIDC_'+key,None)
import server
from connectors import REGISTRY, fetch
PASSWORD='correct horse battery staple'

class DashboardTests(unittest.TestCase):
    def setUp(self):
        with server.db() as c:
            c.execute('DELETE FROM users')
            c.execute('DELETE FROM trust_certificates')
            c.execute("DELETE FROM settings WHERE key IN ('oidc','ui')")
        server.ATTEMPTS.clear()
        server.POLL_LOCKS.clear()
        self.client=TestClient(server.app)
        self.assertEqual(self.client.post('/api/setup',json={'username':'admin','password':PASSWORD}).status_code,200)

    def group(self,name='Infrastructure'):
        r=self.client.post('/api/groups',json={'name':name});self.assertEqual(r.status_code,200);return r.json()['id']

    def card(self,**extra):
        r=self.client.post('/api/cards',json={'name':'Proxmox','url':'https://pve.example.net',**extra});self.assertEqual(r.status_code,200,r.text);return r.json()['id']

    def connection(self,**extra):
        r=self.client.post('/api/connections',json={'name':'Availability','kind':'http','base_url':'https://service.example.net',**extra});self.assertEqual(r.status_code,200,r.text);return r.json()['id']

    def test_ping_connection_validation(self):
        ident=self.connection(kind='ping',base_url='2001:db8::1')
        self.assertTrue(ident)
        for target in ('https://example.net','-c5','host;id'):
            response=self.client.post('/api/connections',json={'name':'Ping','kind':'ping','base_url':target})
            self.assertEqual(response.status_code,422)
        response=self.client.post('/api/connections',json={'name':'Web','kind':'http','base_url':'example.net'})
        self.assertEqual(response.status_code,422)

    def test_setup_login_and_boundaries(self):
        other=TestClient(server.app)
        self.assertEqual(other.get('/api/catalog').status_code,401)
        self.assertEqual(other.post('/api/setup',json={'username':'second','password':PASSWORD}).status_code,409)
        self.assertEqual(other.post('/api/login',json={'username':'admin','password':'wrong'}).status_code,401)
        self.assertEqual(other.post('/api/login',json={'username':'admin','password':PASSWORD}).status_code,200)
        self.assertEqual(self.client.post('/api/groups',json={'name':'x'},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/logout',content='x',headers={'Content-Type':'text/plain'}).status_code,415)

    def test_password_change_invalidates_other_session(self):
        other=TestClient(server.app);other.post('/api/login',json={'username':'admin','password':PASSWORD})
        r=self.client.post('/api/password',json={'current':PASSWORD,'new':'another long test password'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(other.get('/api/catalog').status_code,401)
        self.assertEqual(self.client.get('/api/catalog').status_code,200)

    def test_card_edit_and_group_removal_preserves_card(self):
        gid=self.group();aid=self.card(group_id=gid,description='Virtual machines',icon='server')
        self.assertEqual(self.client.put('/api/cards/'+aid,json={'name':'PVE','url':'https://pve.example.net/#v1','description':'Updated','group_id':gid}).status_code,200)
        self.client.request('DELETE','/api/groups/'+gid,json={})
        cards=self.client.get('/api/catalog').json()['cards']
        self.assertEqual(cards[0]['name'],'PVE');self.assertIsNone(cards[0]['group_id'])
        self.client.request('DELETE','/api/cards/'+aid,json={})
        self.assertEqual(self.client.get('/api/catalog').json()['cards'],[])

    def test_order_requires_exact_owned_items(self):
        a=self.card();b=self.card(name='Second')
        self.assertEqual(self.client.put('/api/order/cards',json={'ids':[b,a]}).status_code,200)
        self.assertEqual([c['id'] for c in self.client.get('/api/catalog').json()['cards']],[b,a])
        for ids in ([a],[a,a],['foreign',b]):
            self.assertEqual(self.client.put('/api/order/cards',json={'ids':ids}).status_code,400)
        ga=self.group();gb=self.group('Home')
        self.assertEqual(self.client.put('/api/order/groups',json={'ids':[gb,ga]}).status_code,200)
        self.assertEqual(self.client.get('/api/catalog').json()['groups'][0]['id'],gb)

    def test_user_isolation(self):
        gid=self.group();cid=self.connection();aid=self.card(group_id=gid,connection_id=cid)
        self.client.post('/api/users',json={'username':'bob','password':PASSWORD})
        other=TestClient(server.app);other.post('/api/login',json={'username':'bob','password':PASSWORD})
        self.assertEqual(other.get('/api/catalog').json(),{'groups':[],'cards':[],'connections':[]})
        self.assertEqual(other.get('/api/status').json(),{})
        self.assertEqual(other.get('/api/settings').status_code,403)
        self.assertEqual(other.post('/api/cards',json={'name':'stolen','url':'https://example.net','connection_id':cid}).status_code,404)
        for table,ident in [('cards',aid),('groups',gid),('connections',cid)]:
            self.assertEqual(other.request('DELETE',f'/api/{table}/{ident}',json={}).status_code,404)
        self.assertEqual(other.post('/api/connections/'+cid+'/test',json={}).status_code,404)

    def test_validation(self):
        for url in ('javascript:alert(1)','file:///etc/passwd','https://user:secret@example.net','https://example.net:99999'):
            self.assertEqual(self.client.post('/api/cards',json={'name':'bad','url':url}).status_code,422)
        self.assertEqual(self.client.post('/api/groups',json={'name':'  '}).status_code,422)
        self.assertEqual(self.client.post('/api/connections',json={'name':'x','kind':'unknown','base_url':'https://example.net'}).status_code,422)
        self.assertEqual(self.client.post('/api/connections',json={'name':'x','kind':'homeassistant','base_url':'https://example.net','secret':'token','config':{'entities':'../../etc'}}).status_code,400)

    def test_encrypted_secret_retention_and_connection_deletion(self):
        secret='test-api-token-value'
        cid=self.connection(kind='proxmox',secret=secret)
        result=self.client.get('/api/catalog').json()['connections'][0]
        self.assertTrue(result['secret_configured']);self.assertNotIn(secret,json.dumps(result));self.assertNotIn('secret_enc',result)
        with server.db() as c:
            stored=c.execute('SELECT secret_enc FROM connections WHERE id=?',(cid,)).fetchone()[0]
        self.assertNotEqual(stored,secret);self.assertEqual(server.secret_cipher().decrypt(stored.encode()).decode(),secret)
        self.assertEqual(self.client.put('/api/connections/'+cid,json={'name':'Renamed','kind':'proxmox','base_url':'https://service.example.net'}).status_code,200)
        with server.db() as c:
            self.assertEqual(c.execute('SELECT secret_enc FROM connections WHERE id=?',(cid,)).fetchone()[0],stored)
        self.card(connection_id=cid)
        self.client.request('DELETE','/api/connections/'+cid,json={})
        self.assertIsNone(self.client.get('/api/catalog').json()['cards'][0]['connection_id'])

    def test_tokens_require_https_and_no_header_injection(self):
        for url,secret in [('http://example.net','token'),('https://example.net','a\r\nb')]:
            r=self.client.post('/api/connections',json={'name':'x','kind':'proxmox','base_url':url,'secret':secret})
            self.assertEqual(r.status_code,400)

    def test_poll_cache_and_stale_retention(self):
        cid=self.connection()
        with server.db() as c: row=dict(c.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone())
        calls=[]
        def handler(request):
            calls.append(request);return httpx.Response(200,text='OK')
        with patch('server.connector_client',lambda ca:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            async def run():
                await server.poll_connection(row)
                await server.poll_connection(row)
            asyncio.run(run())
        self.assertEqual(len(calls),1)
        self.assertEqual(self.client.get('/api/status').json()[cid]['status'],'healthy')
        def failed(request):raise httpx.ConnectError('contains-secret')
        with patch('server.connector_client',lambda ca:httpx.AsyncClient(transport=httpx.MockTransport(failed))):
            asyncio.run(server.poll_connection(row,force=True))
        value=self.client.get('/api/status').json()[cid]
        self.assertEqual(value['status'],'unavailable');self.assertTrue(value['stale']);self.assertEqual(value['metrics'][0]['value'],'200')
        self.assertNotIn('contains-secret',json.dumps(value))

    def test_oidc_persistence_normalization_and_secret_mask(self):
        payload={'enabled':True,'issuer':'https://id.example.net/realms/home/.well-known/openid-configuration','client_id':'homedash','client_secret':'sso-secret'}
        r=self.client.put('/api/settings/oidc',json=payload)
        self.assertEqual(r.status_code,200,r.text)
        value=r.json()['settings'];self.assertEqual(value['oidc']['issuer'],'https://id.example.net/realms/home')
        self.assertTrue(value['oidc_secret_configured']);self.assertNotIn('sso-secret',r.text)
        del payload['client_secret'];self.client.put('/api/settings/oidc',json=payload)
        self.assertTrue(self.client.get('/api/settings').json()['oidc_secret_configured'])

    def test_oidc_maps_matching_local_account(self):
        with server.db() as c: uid=c.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        mapped=server.linked_oidc_user('https://id.example.net|subject','admin')
        self.assertEqual(mapped['id'],uid);self.assertTrue(mapped['admin'])
        self.assertEqual(server.linked_oidc_user('https://id.example.net|subject','renamed')['id'],uid)

    def test_user_delete_cascades_private_data(self):
        self.client.post('/api/users',json={'username':'bob','password':PASSWORD})
        bob=TestClient(server.app);bob.post('/api/login',json={'username':'bob','password':PASSWORD})
        uid=bob.get('/api/state').json()['user']['id']
        bob.post('/api/cards',json={'name':'bob service','url':'https://example.net'})
        self.client.request('DELETE','/api/users/'+uid,json={})
        self.assertEqual(bob.get('/api/catalog').status_code,401)
        with server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM cards WHERE owner=?',(uid,)).fetchone()[0],0)

class ConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_homeassistant_entities_and_auth(self):
        seen=[]
        def handler(request):
            seen.append(request)
            return httpx.Response(200,json={'state':'21.5','attributes':{'friendly_name':'Living room','unit_of_measurement':'°C'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await REGISTRY['homeassistant'].poll(client,'https://ha.example.net',{'entities':'sensor.temperature'},'secret')
        self.assertEqual(seen[0].headers['Authorization'],'Bearer secret')
        self.assertEqual(str(seen[0].url),'https://ha.example.net/api/states/sensor.temperature')
        self.assertEqual(result.metrics[0]['unit'],'°C');self.assertEqual(result.status,'healthy')

    async def test_proxmox_aggregation_and_auth(self):
        def handler(request):
            self.assertEqual(request.headers['Authorization'],'PVEAPIToken=user@pve!dash=token')
            return httpx.Response(200,json={'data':[{'type':'node','status':'online','mem':2*1024**3},{'type':'node','status':'offline'},{'type':'qemu','status':'running'},{'type':'lxc','status':'stopped'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await REGISTRY['proxmox'].poll(client,'https://pve.example.net',{},'user@pve!dash=token')
        self.assertEqual(result.status,'degraded');self.assertEqual(result.metrics[1]['value'],'1 / 2')

    async def test_bounded_payload(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*(2*1024*1024+1)))) as client:
            with self.assertRaises(ValueError):await fetch(client,'https://example.net')


class HTTPRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_is_reachable_without_following(self):
        calls=[]
        def handler(request):
            calls.append(request);return httpx.Response(302,headers={'Location':'https://elsewhere.example.net'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=False) as client:
            result=await REGISTRY['http'].poll(client,'https://example.net',{},'')
        self.assertEqual(result.status,'healthy');self.assertEqual(result.metrics[0]['value'],'302');self.assertEqual(len(calls),1)


class InlineServiceTests(unittest.TestCase):
    setUp=DashboardTests.setUp
    card=DashboardTests.card
    connection=DashboardTests.connection
    def test_atomic_inline_creation_and_validation(self):
        check={'name':'Portal','kind':'http','base_url':'https://portal.example.net'}
        aid=self.card(connection=check)
        catalog=self.client.get('/api/catalog').json()
        self.assertEqual(len(catalog['connections']),1)
        self.assertEqual(catalog['cards'][0]['connection_id'],catalog['connections'][0]['id'])
        before=catalog
        r=self.client.put('/api/cards/'+aid,json={'name':'Changed','url':'https://changed.example.net','connection':{**check,'kind':'proxmox'}})
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.client.get('/api/catalog').json(),before)

    def test_shared_inline_edit_copies_and_retains_token(self):
        cid=self.connection(kind='proxmox',secret='retain-this-token')
        a=self.card(connection_id=cid);self.card(name='Second',connection_id=cid)
        body={'name':'Edited','url':'https://pve.example.net','connection_id':cid,'connection':{'name':'Edited check','kind':'proxmox','base_url':'https://new.example.net'}}
        r=self.client.put('/api/cards/'+a,json=body);self.assertEqual(r.status_code,200,r.text)
        catalog=self.client.get('/api/catalog').json();new_id=next(c['connection_id'] for c in catalog['cards'] if c['id']==a)
        self.assertNotEqual(new_id,cid)
        with server.db() as c:
            old=c.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone()
            new=c.execute('SELECT * FROM connections WHERE id=?',(new_id,)).fetchone()
        self.assertEqual(old['base_url'],'https://service.example.net')
        self.assertEqual(server.secret_cipher().decrypt(new['secret_enc'].encode()).decode(),'retain-this-token')
        self.client.put('/api/cards/'+a,json={'name':'No check','url':'https://pve.example.net'})
        self.assertIsNone(next(c['connection_id'] for c in self.client.get('/api/catalog').json()['cards'] if c['id']==a))

class JSONConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_mapping_and_token(self):
        from connectors import JSONDefinition,JSONConnector
        definition=JSONDefinition.model_validate({'id':'custom_demo','name':'Demo','path':'/api/status','auth':'bearer','metrics':[{'label':'Temperature','pointer':'/a~1b/0/temp','unit':'°C'}]})
        def handler(request):
            self.assertEqual(str(request.url),'https://example.net/api/status')
            self.assertEqual(request.headers['Authorization'],'Bearer token')
            return httpx.Response(200,json={'a/b':[{'temp':21}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await JSONConnector(definition).poll(client,'https://example.net',{},'token')
        self.assertEqual(result.metrics,[{'label':'Temperature','value':'21','unit':'°C'}])

    async def test_json_definition_rejects_cross_origin_and_code(self):
        from connectors import JSONDefinition
        base={'id':'custom_demo','name':'Demo','path':'/api','metrics':[{'label':'x','pointer':'/x'}]}
        for path in ('//evil.example/api','https://evil.example/api','/\\evil','/api\r\nheader'):
            with self.assertRaises(ValueError):JSONDefinition.model_validate({**base,'path':path})
        with self.assertRaises(ValueError):JSONDefinition.model_validate({**base,'script':'print(1)'})

class DefinitionReloadTests(unittest.TestCase):
    def test_hot_reload_and_invalid_edit_retains_last_working(self):
        import connectors
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'demo.json'
            value={'id':'custom_reload','name':'First','path':'/api','metrics':[{'label':'x','pointer':'/x'}]}
            path.write_text(json.dumps(value))
            try:
                self.assertTrue(connectors.load_json_connectors(folder))
                self.assertEqual(connectors.REGISTRY['custom_reload'].name,'First')
                value['name']='Second version';path.write_text(json.dumps(value))
                self.assertTrue(connectors.load_json_connectors(folder))
                self.assertEqual(connectors.REGISTRY['custom_reload'].name,'Second version')
                path.write_text('{invalid')
                with self.assertRaises(ValueError):connectors.load_json_connectors(folder)
                self.assertEqual(connectors.REGISTRY['custom_reload'].name,'Second version')
                path.unlink();connectors.load_json_connectors(folder)
                self.assertNotIn('custom_reload',connectors.REGISTRY)
            finally:
                connectors.REGISTRY.pop('custom_reload',None)
                connectors.JSON_IDS=set();connectors.JSON_SIGNATURE=None


class TrustTests(unittest.TestCase):
    setUp=DashboardTests.setUp
    connection=DashboardTests.connection

    @staticmethod
    def certificate(name):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from datetime import datetime,timedelta,timezone
        key=ec.generate_private_key(ec.SECP256R1())
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])
        now=datetime.now(timezone.utc)
        cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=30)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256())
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    def test_multiple_certificates_dedup_and_admin_only(self):
        pem=self.certificate('Root A')+self.certificate('Root B')
        r=self.client.post('/api/settings/trust',json={'name':'Homelab roots','certificate':pem})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['added'],2)
        self.assertEqual(self.client.post('/api/settings/trust',json={'name':'Again','certificate':pem}).json()['added'],0)
        listed=self.client.get('/api/settings/trust').json();self.assertEqual(len(listed),2)
        self.assertTrue(all('expires' in v and 'fingerprint' in v for v in listed))
        self.client.post('/api/users',json={'username':'bob','password':PASSWORD})
        other=TestClient(server.app);other.post('/api/login',json={'username':'bob','password':PASSWORD})
        self.assertEqual(other.get('/api/settings/trust').status_code,403)
        self.assertEqual(other.post('/api/settings/trust',json={'name':'x','certificate':pem}).status_code,403)
        self.assertEqual(other.request('DELETE','/api/settings/trust/'+listed[0]['fingerprint'],json={}).status_code,403)
        self.assertEqual(self.client.request('DELETE','/api/settings/trust/'+listed[0]['fingerprint'],json={}).status_code,200)
        self.assertEqual(len(self.client.get('/api/settings/trust').json()),1)

    def test_rejects_invalid_and_private_key_even_in_bundle(self):
        import base64
        for text in ('not a certificate',self.certificate('Root')+'-----BEGIN PRIVATE KEY-----\nanything\n-----END PRIVATE KEY-----'):
            for payload in ({'certificate':text},{'certificate':base64.b64encode(text.encode()).decode(),'encoding':'base64'}):
                self.assertEqual(self.client.post('/api/settings/trust',json={'name':'bad',**payload}).status_code,400)
        self.assertEqual(self.client.get('/api/settings/trust').json(),[])

    def test_global_trust_reaches_checks_and_rechecks_after_change(self):
        cid=self.connection()
        with server.db() as c:row=dict(c.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone())
        seen=[]
        def transport(ca):
            seen.append(ca)
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='ok')))
        pem=self.certificate('Global Root')
        self.client.post('/api/settings/trust',json={'name':'Global','certificate':pem})
        with patch('server.connector_client',transport):asyncio.run(server.poll_connection(row))
        self.assertIn(pem.strip(),seen[0])
        self.assertEqual(self.client.get('/api/status').json()[cid]['status'],'healthy')
        fp=self.client.get('/api/settings/trust').json()[0]['fingerprint']
        self.client.request('DELETE','/api/settings/trust/'+fp,json={})
        self.assertEqual(self.client.get('/api/status').json()[cid]['status'],'pending')

    def test_tls_failure_is_actionable_and_no_raw_error_leaks(self):
        cid=self.connection()
        with server.db() as c:row=dict(c.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone())
        def failed(request):raise httpx.ConnectError('CERTIFICATE_VERIFY_FAILED secret-data')
        with patch('server.connector_client',lambda ca:httpx.AsyncClient(transport=httpx.MockTransport(failed))):
            result=asyncio.run(server.poll_connection(row,force=True))
        self.assertEqual(result['error_code'],'tls_untrusted')
        self.assertIn('Server settings → Trust',result['summary'])
        self.assertNotIn('secret-data',json.dumps(result))

    def test_der_upload_and_oidc_uses_shared_trust(self):
        import base64
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        pem=self.certificate('Provider CA')
        der=x509.load_pem_x509_certificate(pem.encode()).public_bytes(serialization.Encoding.DER)
        r=self.client.post('/api/settings/trust',json={'name':'Provider','certificate':base64.b64encode(der).decode(),'encoding':'base64'})
        self.assertEqual(r.status_code,200,r.text)
        self.client.put('/api/settings/oidc',json={'enabled':True,'issuer':'https://id.example.net','client_id':'test','client_secret':'secret'})
        with patch('server.configured_oauth') as configured:
            server.oidc_runtime()
            options=json.loads(configured.call_args[0][0])
        self.assertIn(pem.strip(),options['ca_pem'])

    def test_legacy_service_ca_is_retained_on_edit(self):
        pem=self.certificate('Legacy')
        cid=self.connection(ca_pem=pem)
        r=self.client.put('/api/connections/'+cid,json={'name':'Renamed','kind':'http','base_url':'https://service.example.net'})
        self.assertEqual(r.status_code,200)
        with server.db() as c:self.assertEqual(c.execute('SELECT ca_pem FROM connections WHERE id=?',(cid,)).fetchone()[0],pem)



class CheckPollingTests(unittest.TestCase):
    setUp=DashboardTests.setUp
    connection=DashboardTests.connection
    card=DashboardTests.card
    def test_only_attached_checks_are_polled(self):
        from unittest.mock import AsyncMock
        detached=self.connection(name='Detached')
        attached=self.connection(name='Attached')
        self.card(connection_id=attached)
        poll=AsyncMock()
        with patch('server.poll_connection',poll),patch('server.asyncio.sleep',side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):asyncio.run(server.poll_loop())
        self.assertEqual([call.args[0]['id'] for call in poll.call_args_list],[attached])


class UISettingsTests(unittest.TestCase):
    setUp=DashboardTests.setUp
    def test_auto_default_and_anonymous_login_theme(self):
        anonymous=TestClient(server.app)
        self.assertEqual(anonymous.get('/api/state').json()['ui']['theme'],'auto')
        self.assertEqual(self.client.put('/api/settings/ui',json={'theme':'light'}).status_code,200)
        self.assertIn("HOME_UI_DEFAULT='light'",anonymous.get('/').text)
        self.assertEqual(anonymous.get('/api/state').json()['ui']['theme'],'light')
        self.assertEqual(anonymous.put('/api/settings/ui',json={'theme':'dark'}).status_code,401)
        self.assertEqual(self.client.put('/api/settings/ui',json={'theme':'bad'}).status_code,422)

if __name__=='__main__':unittest.main()
