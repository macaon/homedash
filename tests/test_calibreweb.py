import asyncio
import unittest
import httpx
from connectors import REGISTRY

FORM = '<form><input type="hidden" name="csrf_token" value="fixture-csrf"><input name="username"></form>'


class CalibreWebTests(unittest.TestCase):
    def poll(self, password='fixture-password', allowed=True, login_status=None, rows=({'atom_timestamp': '2025-08-24T20:23:25+00:00', 'title': 'Fixture'},)):
        def handler(request):
            path = request.url.path
            if path == '/login' and request.method == 'GET':
                return httpx.Response(200, text=FORM, headers={'set-cookie': 'session=fixture-session; Path=/'})
            if path == '/login':
                form = dict(pair.split('=') for pair in request.content.decode().split('&'))
                self.assertEqual(form['csrf_token'], 'fixture-csrf')
                if login_status:
                    return httpx.Response(login_status)
                ok = form['username'] == 'reader' and form['password'] == 'fixture-password'
                return httpx.Response(302, headers={'location': '/'}) if ok else httpx.Response(200, text=FORM)
            self.assertEqual(path, '/ajax/listbooks')
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.url.params['limit'], '1')
            self.assertIn('session=fixture-session', request.headers.get('cookie', ''))
            self.assertNotIn('fixture-password', str(request.url))
            if not allowed:
                return httpx.Response(302, headers={'location': '/login'})
            return httpx.Response(200, json={'total': 12, 'totalNotFiltered': 1094, 'rows': list(rows)})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                return await REGISTRY['calibreweb'].poll(client, 'https://books.example', {'username': 'reader'}, password)
        return asyncio.run(run())

    def test_book_total(self):
        result = self.poll()
        self.assertEqual((result.status, result.metrics), ('healthy', [{'label': 'Books', 'value': '1,094'},
                                                                       {'label': 'Last added', 'value': '2025-08-24'}]))
        self.assertNotIn('Fixture', str(result))

    def test_empty_library_or_missing_timestamp_still_shows_the_total(self):
        for rows in ((), ({'title': 'x'},)):
            self.assertEqual(self.poll(rows=rows).metrics, [{'label': 'Books', 'value': '1,094'}])

    def test_clear_errors(self):
        for kwargs, message in (({'password': 'wrong'}, 'rejected the username or password'),
                                ({'allowed': False}, 'Show books list'),
                                ({'login_status': 429}, 'limiting sign-ins')):
            with self.assertRaisesRegex(ValueError, message): self.poll(**kwargs)

    def test_username_is_required(self):
        connector = REGISTRY['calibreweb']
        self.assertEqual(connector.validate({'username': ' reader '}), {'username': 'reader'})
        with self.assertRaises(ValueError): connector.validate({'username': ' '})

if __name__ == '__main__':
    unittest.main()
