import asyncio
import unittest
import httpx
from connectors import REGISTRY
from connectors.audiobookshelf import activity


class AudiobookshelfTests(unittest.TestCase):
    def poll(self, sessions=None, history=True, fail=None):
        self.calls = []

        def handler(request):
            self.calls.append(request)
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-secret')
            self.assertNotIn('fixture-secret', str(request.url))
            path = request.url.path
            if path == fail:
                return httpx.Response(403)
            if path == '/api/libraries':
                data = {'libraries': [{'id': 'english'}, {'id': 'swedish'}]}
            elif path.endswith('/stats'):
                data = {'totalItems': 1169 if '/english/' in path else 226}
            elif path == '/api/users/online':
                data = {'usersOnline': [{'id': 'reader', 'username': 'Reader'}], 'openSessions': sessions or []}
            elif path == '/api/me/listening-sessions':
                self.assertEqual(request.url.params['itemsPerPage'], '1')
                data = {'sessions': [{'displayTitle': 'Recent book', 'duration': 3600, 'currentTime': 1800}] if history else []}
            else:
                self.fail('Unexpected request: ' + path)
            return httpx.Response(200, json=data)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['audiobookshelf'].poll(client, 'https://abs.example', {}, 'fixture-secret')
        return asyncio.run(run())

    def test_idle_uses_recent_history_not_online_user_as_listener(self):
        result = self.poll()
        self.assertEqual(result.metrics[0]['value'], '0')
        self.assertEqual(result.metrics[1]['value'], '1,395')
        self.assertEqual(result.details['heading'], 'Last listened')
        self.assertEqual(result.details['items'][0]['progress'], 50)
        self.assertNotIn('fixture-secret', str(result))

    def test_sessions_include_users_and_all_rows_for_disclosure(self):
        result = self.poll(sessions=[{'userId': 'reader', 'displayTitle': 'Book ' + str(i),
                                    'duration': 100, 'currentTime': 150} for i in range(7)])
        self.assertEqual(result.metrics[0]['value'], '7')
        self.assertEqual(len(result.details['items']), 7)
        self.assertEqual(result.details['items'][0]['user'], 'Reader')
        self.assertEqual(result.details['items'][0]['progress'], 100)
        self.assertEqual(result.details['items'][0]['remaining_minutes'], 0)
        self.assertFalse(any(r.url.path == '/api/me/listening-sessions' for r in self.calls))

    def test_empty_history(self):
        result = self.poll(history=False)
        self.assertEqual(result.details['items'], [])
        self.assertIn('No listening history', result.details['empty'])

    def test_missing_permissions_fail_instead_of_reporting_zero(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.poll(fail='/api/users/online')

    def test_missing_and_invalid_durations(self):
        self.assertNotIn('progress', activity({'displayTitle': 'No duration'}))
        for value in (float('nan'), float('inf'), '100'):
            with self.assertRaises(ValueError):
                activity({'duration': value})
