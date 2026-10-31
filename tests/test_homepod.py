import asyncio
import unittest
import httpx
from connectors import REGISTRY


def container(name, state='running', status='Up 2 days'):
    return {'Names': name, 'State': state, 'Status': status, 'Labels': 'secret=do-not-return'}


def alert(name, host='Local', kind='unhealthy'):
    return {'kind': 'container-' + kind, 'level': 'warn', 'title': name, 'detail': kind.capitalize() + ' · ' + host,
            'host': host, 'container': name, 'ignorable': True}


class HomePodTests(unittest.TestCase):
    def poll(self, hosts, containers, report=None, offline=(), fail=None, attention_status=200):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-token')
            self.assertNotIn('fixture-token', str(request.url))
            if fail:
                return httpx.Response(fail)
            if request.url.path == '/api/hosts':
                return httpx.Response(200, json=hosts)
            if request.url.path == '/api/attention':
                return httpx.Response(attention_status, json=report if report is not None else {'active': [], 'ignored': []})
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

    def test_healthy_fleet_counts_stopped_containers_without_flagging_them(self):
        result = self.poll(self.hosts('Local', 'Remote'), {
            'local': [container('web'), container('kometa', 'exited', 'Exited (0) 10 days ago')],
            'remote': [container('db', status='Up 3 days (healthy)')]})
        self.assertEqual(result.status, 'healthy')
        self.assertEqual([m['value'] for m in result.metrics], ['2 of 2', '2 of 3', '1'])
        self.assertEqual(result.details['items'], [])
        self.assertNotIn('note', result.details)

    def test_what_needs_attention_is_whatever_homepod_reports(self):
        report = {'active': [alert('mousehole'),
                             {'kind': 'host-offline', 'level': 'bad', 'title': 'Cineplex', 'detail': 'Host offline', 'host': 'Cineplex', 'ignorable': False},
                             {'kind': 'updates-available', 'level': 'info', 'title': '2 image updates available', 'detail': 'Review before updating.', 'host': None, 'ignorable': False}],
                  'ignored': []}
        result = self.poll(self.hosts('Local'), {'local': [container('mousehole')]}, report)
        self.assertEqual(result.status, 'degraded')
        self.assertEqual(result.summary, '2 item(s) need attention')
        self.assertEqual([(r['title'], r['subtitle'], r['state']) for r in result.details['items']],
                         [('mousehole', 'Local', 'Unhealthy'), ('Cineplex', 'Cineplex', 'Host offline'),
                          ('2 image updates available', 'Review before updating.', 'Notice')])
        self.assertNotIn('do-not-return', str(result))

    def test_notices_alone_do_not_degrade_and_ignored_alerts_are_acknowledged(self):
        notice = {'kind': 'updates-available', 'level': 'info', 'title': '1 image update available', 'detail': 'Review.', 'host': None}
        result = self.poll(self.hosts('Local'), {'local': []}, {'active': [notice], 'ignored': [{'container': 'mousehole'}, {'container': 'x'}]})
        self.assertEqual(result.status, 'healthy')
        self.assertEqual(result.details['note'], '2 alert(s) ignored in HomePod.')
        self.assertEqual(len(result.details['items']), 1)

    def test_unreachable_hosts_lower_the_online_count_without_failing_the_card(self):
        result = self.poll(self.hosts('Local', 'Agent', 'Broken', offline_agent='Agent'),
                           {'local': [container('web')], 'agent': [], 'broken': []}, offline=('broken',))
        # The offline agent is not even queried; the unreachable host answered 503.
        self.assertEqual(result.metrics[0]['value'], '1 of 3')

    def test_token_problem_is_unavailable_not_empty(self):
        for status in (401, 403):
            with self.assertRaises(httpx.HTTPStatusError):
                self.poll(self.hosts('Local'), {}, fail=status)
        with self.assertRaises(httpx.HTTPStatusError):  # a HomePod too old to report attention
            self.poll(self.hosts('Local'), {'local': []}, attention_status=404)

    def test_malformed_reports_and_bad_host_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            self.poll(self.hosts('Local'), {'local': []}, report={'active': 'nope', 'ignored': []})
        with self.assertRaises(ValueError):
            self.poll([{'id': '../x', 'name': 'Bad'}], {})
