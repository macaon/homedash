import asyncio
import unittest
import httpx
from connectors import REGISTRY

QUEUE = {'queueStatus': {'isPaused': False, 'isActive': False},
         'jobCounts': {'active': 0, 'completed': 0, 'failed': 0, 'delayed': 0, 'waiting': 0, 'paused': 0}}


class ImmichTests(unittest.TestCase):
    def poll(self, failed=0, waiting=0, percent=58, status=200):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['x-api-key'], 'fixture-key')
            self.assertNotIn('fixture-key', str(request.url))
            if status != 200:
                return httpx.Response(status)
            counts = dict(QUEUE['jobCounts'], failed=failed, waiting=waiting)
            return httpx.Response(200, json={
                '/api/server/statistics': {'photos': 22978, 'videos': 1207, 'usage': 182305289531},
                '/api/server/storage': {'diskUsagePercentage': percent, 'diskSizeRaw': 12439599681536},
                '/api/jobs': {'thumbnailGeneration': {**QUEUE, 'jobCounts': counts}, 'smartSearch': QUEUE},
            }[request.url.path])
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['immich'].poll(client, 'https://photos.example', {}, 'fixture-key')
        return asyncio.run(run())

    def test_healthy_idle_server(self):
        result = self.poll()
        self.assertEqual(result.status, 'healthy')
        self.assertEqual({m['label']: m['value'] for m in result.metrics}, {
            'Photos': '22,978', 'Videos': '1,207', 'Library size': '169.8 GiB',
            'Disk used': '58% of 11.3 TiB'})
        self.assertEqual({m['label']: m['value'] for m in result.details['sections'][0]['metrics']},
                         {'Jobs pending': '0', 'Failed jobs': '0'})

    def test_pending_jobs_are_not_a_problem(self):
        result = self.poll(waiting=40)
        self.assertEqual(result.status, 'healthy')
        self.assertEqual(result.details['sections'][0]['metrics'][0]['value'], '40')

    def test_failed_jobs_and_full_disk_need_attention(self):
        self.assertEqual(self.poll(failed=2).summary, '2 failed job(s)')
        result = self.poll(percent=93)
        self.assertEqual(result.status, 'degraded')
        self.assertEqual(result.summary, 'Disk 93% full')

    def test_permission_failure_is_not_empty(self):
        with self.assertRaises(httpx.HTTPStatusError): self.poll(status=403)

if __name__ == '__main__':
    unittest.main()
