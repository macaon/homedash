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
            c.execute("DELETE FROM settings WHERE key='oidc'")
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

if __name__=='__main__':unittest.main()
