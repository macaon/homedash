"""Read-only host and container health from HomePod's API (a Read-level token is enough).

What needs attention, and what has been ignored, is HomePod's own decision (/api/attention); this only displays it.
"""
import asyncio
import re

import httpx


class HomePod:
    id = 'homepod'
    name = 'HomePod'
    description = 'Docker hosts online, containers running and what needs attention across all HomePod hosts, as HomePod reports it. Needs a Read API token.'
    secret_label = 'API token'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'Authorization': 'Bearer ' + secret}
        hosts = await fetch(client, base + '/api/hosts', headers)
        if not isinstance(hosts, list) or len(hosts) > 32:
            raise ValueError('Expected up to 32 hosts')
        for host in hosts:
            if not re.fullmatch(r'[A-Za-z0-9]{1,64}', str(host['id'])):
                raise ValueError('Invalid host ID')

        async def containers(host):
            agent = host.get('agent')
            if agent is not None and not agent.get('connected'):
                return None  # an offline agent cannot answer; do not wait for it
            try:
                data = await fetch(client, base + '/api/hosts/' + host['id'] + '/containers', headers)
            except httpx.HTTPStatusError as error:
                if error.response.status_code >= 500:
                    return None  # this host is unreachable; a 401/403 is a token problem and propagates
                raise
            if not isinstance(data, list):
                raise ValueError('Expected a container list')
            return data

        results = []
        for offset in range(0, len(hosts), 4):
            results.extend(await asyncio.gather(*(containers(h) for h in hosts[offset:offset + 4])))
        online = running = other = 0
        for found in results:
            if found is not None:
                online += 1
                for container in found:
                    if str(container.get('State') or '').lower() == 'running':
                        running += 1
                    else:
                        other += 1
        report = await fetch(client, base + '/api/attention', headers)
        if not isinstance(report, dict) or not isinstance(report.get('active'), list) or not isinstance(report.get('ignored'), list):
            raise ValueError('Expected an attention report')
        items = [{'title': str(i['title'])[:250], 'subtitle': str(i.get('host') or '')[:100],
                  'state': (str(i['detail']).split(' · ')[0] if i.get('ignorable') else str(i['detail']))[:250]}
                 for i in report['active'] if i.get('level') != 'info']
        notices = [{'title': str(i['title'])[:250], 'subtitle': str(i['detail'])[:250], 'state': 'Notice'}
                   for i in report['active'] if i.get('level') == 'info']
        metrics = [
            {'label': 'Hosts online', 'value': f'{online} of {len(hosts)}'},
            {'label': 'Containers running', 'value': f'{running:,} of {running + other:,}'},
            {'label': 'Not running', 'value': f'{other:,}'},
        ]
        details = {'heading': 'Needs attention', 'items': (items + notices)[:100], 'empty': 'Nothing needs attention.', 'more_label': 'More items'}
        if report['ignored']:
            details['note'] = f'{len(report["ignored"])} alert(s) ignored in HomePod.'
        return Result('degraded' if items else 'healthy',
                      f'{len(items)} item(s) need attention' if items else 'HomePod API available', metrics, details)
