import asyncio
import unittest
import httpx
from connectors import REGISTRY

LIBRARIES = [{'id': 'a', 'name': 'Manga', 'unavailable': False}, {'id': 'b', 'name': 'Comics', 'unavailable': False}]
TOTALS = {'': 610, 'read_status=UNREAD': 595, 'read_status=IN_PROGRESS': 4, 'media_status=ERROR': 2, 'media_status=UNSUPPORTED': 5}


class KomgaTests(unittest.TestCase):
    def poll(self, libraries=LIBRARIES, status=200):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['x-api-key'], 'fixture-key')
            self.assertNotIn('fixture-key', str(request.url))
            if status != 200:
                return httpx.Response(status)
            if request.url.path == '/api/v1/libraries':
                return httpx.Response(200, json=libraries)
            self.assertEqual(request.url.params['size'], '1')
            if request.url.path == '/api/v1/series':
                return httpx.Response(200, json={'content': [], 'totalElements': 49})
            self.assertEqual(request.url.path, '/api/v1/books')
            query = '&'.join(f'{k}={v}' for k, v in request.url.params.items() if k != 'size')
            return httpx.Response(200, json={'content': [], 'totalElements': TOTALS[query]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['komga'].poll(client, 'https://comics.example', {}, 'fixture-key')
        return asyncio.run(run())

    def test_healthy_server(self):
        result = self.poll()
        self.assertEqual(result.status, 'healthy')
        self.assertEqual({m['label']: m['value'] for m in result.metrics},
                         {'Series': '49', 'Books': '610', 'Unread': '595', 'In progress': '4'})
        self.assertEqual({m['label']: m['value'] for m in result.details['sections'][0]['metrics']},
                         {'Libraries': '2', 'Unavailable': 'None', 'Unreadable books': '2', 'Unsupported books': '5'})

    def test_unavailable_library_needs_attention(self):
        result = self.poll([LIBRARIES[0], {**LIBRARIES[1], 'unavailable': True}])
        self.assertEqual((result.status, result.summary), ('degraded', '1 library(s) unavailable'))
        self.assertEqual(result.details['sections'][0]['metrics'][1]['value'], 'Comics')

    def test_rejected_key_is_not_empty(self):
        with self.assertRaises(httpx.HTTPStatusError): self.poll(status=401)

if __name__ == '__main__':
    unittest.main()
