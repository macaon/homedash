import asyncio
import unittest
import httpx
from connectors import REGISTRY


class AudioBookRequestTests(unittest.TestCase):
    def poll(self, requests=None, manual=None, fail=False):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-key')
            self.assertNotIn('fixture-key', str(request.url))
            if fail:
                return httpx.Response(403)
            self.assertIn(request.url.path, ['/api/requests', '/api/requests/manual'])
            return httpx.Response(200, json=(manual or []) if request.url.path.endswith('/manual') else (requests or []))
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['audiobookrequest'].poll(client, 'https://abr.example', {}, 'fixture-key')
        return asyncio.run(run())

    def book(self, title='A book', filled=False, error=None):
        return {'book': {'title': title, 'authors': ['An author'], 'downloaded': filled},
                'requests': [{'user_username': 'reader', 'updated_at': '2026-10-31T12:00:00'}],
                'download_error': error}

    def test_counts_include_filled_and_manual_requests(self):
        result = self.poll([self.book(), self.book('Filled', True)], [
            {'title': 'Manual pending', 'authors': [], 'downloaded': False, 'user_username': 'another-reader'},
            {'title': 'Manual filled', 'downloaded': True},
        ])
        self.assertEqual([m['value'] for m in result.metrics], ['2', '2', '0'])
        self.assertEqual(result.metrics[1]['label'], 'Filled requests')
        self.assertEqual(result.details['heading'], 'Outstanding requests')
        self.assertEqual(len(result.details['items']), 2)
        self.assertEqual(result.details['items'][0]['user'], 'another-reader')
        self.assertEqual(result.status, 'healthy')

    def test_error_state_does_not_expose_provider_error(self):
        result = self.poll([self.book(error='URL contains fixture-key')])
        self.assertEqual(result.status, 'degraded')
        self.assertEqual(result.metrics[2]['value'], '1')
        self.assertEqual(result.details['items'][0]['state'], 'Download error')
        self.assertNotIn('fixture-key', str(result))

    def test_multiple_requesters_count_as_one_book(self):
        book = self.book()
        book['requests'].append({'user_username': 'other', 'updated_at': '2026-10-30T12:00:00'})
        result = self.poll([book])
        self.assertEqual(result.metrics[0]['value'], '1')
        self.assertEqual(result.details['items'][0]['user'], 'other, reader')
        self.assertEqual(result.details['items'][0]['date'], '2026-10-31')

    def test_filled_requests_do_not_appear_outstanding(self):
        result = self.poll([self.book(filled=True)])
        self.assertEqual(result.details['items'], [])
        self.assertEqual(result.details['empty'], 'No outstanding requests.')

    def test_permission_failure_is_not_empty(self):
        with self.assertRaises(httpx.HTTPStatusError): self.poll(fail=True)

    def test_missing_status_is_not_counted_as_unfilled(self):
        book = self.book()
        del book['book']['downloaded']
        with self.assertRaises(KeyError): self.poll([book])
