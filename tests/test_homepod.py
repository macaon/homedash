import asyncio
import unittest
import httpx
from connectors import REGISTRY


def container(name, state='running', status='Up 2 days'):
    return {'Names': name, 'State': state, 'Status': status, 'Labels': 'secret=do-not-return'}


class HomePodTests(unittest.TestCase):
    def poll(self, hosts, containers, offline=(), fail=None):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-token')
            self.assertNotIn('fixture-token', str(request.url))
            if fail:
                return httpx.Response(fail)
            if request.url.path == '/api/hosts':
                return httpx.Response(200, json=hosts)
            host = request.url.path.split('/')[3]
            if host in offline:
                return httpx.Response(503)
            return httpx.Response(200, json=containers[host])
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['homepod'].poll(client, 'https://pod.example', {}, 'fixture-token')
        return asyncio.run(run())

    def hosts(self, *names, offline_agent=None):
        return [{'id': name.lower(), 'name': name,
                 'agent': {'connected': name != offline_agent} if name != 'Local' else None} for name in names]

    def test_healthy_fleet_and_stopped_jobs_are_not_problems(self):
        result = self.poll(self.hosts('Local', 'Remote'), {
            'local': [container('web'), container('kometa', 'exited', 'Exited (0) 10 days ago')],
            'remote': [container('db', status='Up 3 days (healthy)')]})
        self.assertEqual(result.status, 'healthy')
        self.assertEqual([m['value'] for m in result.metrics], ['2 of 2', '2 of 3', '1'])
        self.assertEqual(result.details['items'], [])
        self.assertEqual(result.details['heading'], 'Needs attention')

    def test_unhealthy_restarting_and_dead_containers_need_attention(self):
        result = self.poll(self.hosts('Local'), {'local': [
            container('a', status='Up 1 hour (unhealthy)'), container('b', 'restarting', 'Restarting (1) 5 seconds ago'),
            container('c', 'dead', 'Dead'), container('d', status='Up 1 hour (health: starting)')]})
        self.assertEqual(result.status, 'degraded')
        self.assertEqual([(r['title'], r['state']) for r in result.details['items']],
                         [('a', 'Unhealthy'), ('b', 'Restarting'), ('c', 'Dead')])
        self.assertEqual(result.details['items'][0]['subtitle'], 'Local')
        self.assertNotIn('do-not-return', str(result))

    def test_offline_agent_and_unreachable_host_are_flagged_without_failing_the_card(self):
        result = self.poll(self.hosts('Local', 'Agent', 'Broken', offline_agent='Agent'),
                           {'local': [container('web')], 'agent': [], 'broken': []}, offline=('broken',))
        # The offline agent is not even queried; the unreachable host answered 503.
        self.assertEqual(result.metrics[0]['value'], '1 of 3')
        self.assertEqual(result.status, 'degraded')
        self.assertEqual({(r['title'], r['state']) for r in result.details['items']},
                         {('Agent', 'Host offline'), ('Broken', 'Host offline')})

    def test_token_problem_is_unavailable_not_empty(self):
        for status in (401, 403):
            with self.assertRaises(httpx.HTTPStatusError):
                self.poll(self.hosts('Local'), {}, fail=status)

    def test_bad_host_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.poll([{'id': '../x', 'name': 'Bad'}], {})
