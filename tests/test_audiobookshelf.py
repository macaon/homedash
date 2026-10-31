import asyncio
import unittest
import time
import httpx
from connectors import REGISTRY
from connectors.audiobookshelf import activity


class AudiobookshelfTests(unittest.TestCase):
    def poll(self, sessions=None, recent=None, fail=None):
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
            elif path == '/api/sessions':
                self.assertEqual(request.url.params['desc'], '1')
                data = {'sessions': recent or []}
            else:
                self.fail('Unexpected request: ' + path)
            return httpx.Response(200, json=data)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['audiobookshelf'].poll(client, 'https://abs.example', {}, 'fixture-secret')
        return asyncio.run(run())

    def test_idle_does_not_fetch_history_or_count_online_users_as_listeners(self):
        result = self.poll()
        self.assertEqual(result.metrics[0]['value'], '0')
        self.assertEqual(result.metrics[1]['value'], '1,395')
        self.assertEqual(result.details['heading'], 'Recent activity')
        self.assertEqual(result.details['items'], [])
        self.assertEqual(result.details['empty'], 'No open or recently synced sessions.')
        self.assertFalse(any('/api/me/' in r.url.path for r in self.calls))
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

    def test_missing_permissions_fail_instead_of_reporting_zero(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.poll(fail='/api/users/online')

    def test_missing_and_invalid_durations(self):
        self.assertNotIn('progress', activity({'displayTitle': 'No duration'}))
        for value in (float('nan'), float('inf'), '100'):
            with self.assertRaises(ValueError):
                activity({'duration': value})

    def test_synced_client_is_visible_without_open_session(self):
        result = self.poll(recent=[{'id':'synced','userId':'another-reader','user':{'username':'Other reader'},
                                   'displayTitle':'Current book','updatedAt':time.time()*1000-45000,
                                   'duration':3600,'currentTime':1800}])
        self.assertEqual(result.metrics[0]['value'],'1')
        self.assertEqual(result.details['items'][0]['state'],'Recently synced')
        self.assertEqual(result.details['items'][0]['user'],'Other reader')
        self.assertEqual(result.details['items'][0]['progress'],50)

    def test_old_and_duplicate_syncs_do_not_become_extra_listeners(self):
        now=time.time()*1000
        result = self.poll(sessions=[{'userId':'reader','displayTitle':'Open book'}],recent=[
            {'userId':'reader','updatedAt':now-1000,'displayTitle':'Same user'},
            {'userId':'second','updatedAt':now-5000,'displayTitle':'Newer'},
            {'userId':'second','updatedAt':now-10000,'displayTitle':'Older'},
            {'userId':'stale','updatedAt':now-181000,'displayTitle':'Old history'},
            {'userId':'future','updatedAt':now+60000,'displayTitle':'Future'},
        ])
        self.assertEqual(result.metrics[0]['value'],'2')
        self.assertEqual([r['title'] for r in result.details['items']],['Open book','Newer'])
        self.assertEqual(result.details['items'][0]['state'],'Open session')

    def test_recent_sessions_permission_failure_is_not_empty_activity(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.poll(fail='/api/sessions')
