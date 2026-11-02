import asyncio
import unittest
import httpx
from connectors import REGISTRY

SERIES = [{'status': 'Active', 'totalIssues': 10, 'name': 'Fixture A'}, {'status': 'Active', 'totalIssues': '5'},
          {'status': 'Paused', 'totalIssues': None}, {'status': 'Ended', 'totalIssues': 3}]


class MylarTests(unittest.TestCase):
    def poll(self, key='fixture-key', wanted=3, upcoming=1, shape='native'):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.url.path, '/api')
            if request.url.params.get('apikey') != 'fixture-key':
                return httpx.Response(200, json={'success': False, 'error': {'code': 460, 'message': 'Incorrect API key'}})
            return httpx.Response(200, json={
                'getIndex': {'success': True, 'data': SERIES},
                'getWanted': {'issues': [{'IssueID': i} for i in range(wanted)]},
                'getUpcoming': [{'IssueID': i} for i in range(upcoming)],
            }[request.url.params['cmd']])
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['mylar'].poll(client, 'https://mylar.example', {}, key)
        return asyncio.run(run())

    def test_totals(self):
        result = self.poll()
        self.assertEqual((result.status, {m['label']: m['value'] for m in result.metrics}), ('healthy', {
            'Series': '4', 'Issues': '18', 'Wanted': '3', 'Upcoming': '1'}))
        self.assertEqual({m['label']: m['value'] for m in result.details['sections'][0]['metrics']},
                         {'Active': '2', 'Paused': '1', 'Ended': '1'})
        self.assertNotIn('Fixture A', str(result))

    def test_wrong_key_is_an_error_not_an_empty_library(self):
        with self.assertRaisesRegex(ValueError, 'Incorrect API key'): self.poll(key='wrong')

    def test_empty_library(self):
        self.assertEqual(self.poll(wanted=0, upcoming=0).metrics[2]['value'], '0')

if __name__ == '__main__':
    unittest.main()
