import asyncio
import unittest
import httpx
from connectors import REGISTRY

TASKS = {'redis_status': 'OK', 'celery_status': 'OK', 'index_status': 'OK', 'classifier_status': 'OK',
         'sanity_check_status': 'OK'}


class PaperlessTests(unittest.TestCase):
    def poll(self, failed=0, free=4000, tasks=None, database='OK', admin=True, status=200):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['authorization'], 'Token fixture-token')
            self.assertNotIn('fixture-token', str(request.url))
            if status != 200:
                return httpx.Response(status)
            if request.url.path == '/api/statistics/':
                return httpx.Response(200, json={'documents_total': 1234, 'documents_inbox': 3, 'inbox_tag': 15})
            self.assertEqual(request.url.path, '/api/status/')
            if not admin:
                return httpx.Response(403)
            return httpx.Response(200, json={
                'storage': {'total': 10000, 'available': free}, 'database': {'status': database},
                'tasks': {**TASKS, **(tasks or {}), 'summary': {'failure_count': failed}}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['paperless'].poll(client, 'https://docs.example', {}, 'fixture-token')
        return asyncio.run(run())

    def test_healthy_server(self):
        result = self.poll()
        self.assertEqual(result.status, 'healthy')
        self.assertEqual({m['label']: m['value'] for m in result.metrics},
                         {'Documents': '1,234', 'Inbox': '3', 'Disk used': '60% of 9.8 KiB'})
        self.assertEqual({m['label']: m['value'] for m in result.details['sections'][0]['metrics']},
                         {'Failed imports (30 days)': '0', 'Components failing': 'None'})

    def test_failures_full_disk_and_broken_components_need_attention(self):
        self.assertEqual(self.poll(failed=2).summary, '2 failed import(s)')
        self.assertEqual(self.poll(free=500).summary, 'Disk 95% full')
        result = self.poll(tasks={'index_status': 'ERROR'}, database='ERROR')
        self.assertEqual((result.status, result.summary), ('degraded', 'Database, Search index failing'))

    def test_disabled_warning_and_classifier_states_are_not_failures(self):
        self.assertEqual(self.poll(tasks={'index_status': 'WARNING', 'sanity_check_status': 'DISABLED', 'classifier_status': 'ERROR'}).status, 'healthy')

    def test_token_without_admin_still_shows_totals(self):
        result = self.poll(admin=False)
        self.assertEqual(result.status, 'healthy')
        self.assertEqual([m['label'] for m in result.metrics], ['Documents', 'Inbox'])
        self.assertEqual(result.details, {})

    def test_rejected_token_is_not_empty(self):
        with self.assertRaises(httpx.HTTPStatusError): self.poll(status=401)

if __name__ == '__main__':
    unittest.main()
