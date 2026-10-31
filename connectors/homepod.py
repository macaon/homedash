"""Read-only host and container health from HomePod's API (a Read-level token is enough)."""
import asyncio
import re

import httpx


def container_problem(container):
    """Only conditions that are never intentional: stopped containers are often one-shot jobs."""
    state = str(container.get('State') or '').lower()
    status = str(container.get('Status') or '').lower()
    if '(unhealthy)' in status:
        return 'Unhealthy'
    if state == 'restarting':
        return 'Restarting'
    if state == 'dead':
        return 'Dead'
    return ''


class HomePod:
    id = 'homepod'
    name = 'HomePod'
    description = 'Docker hosts online, containers running and any unhealthy or restarting containers across all HomePod hosts. Needs a Read API token.'
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
        problems = []
        for host, found in zip(hosts, results):
            name = str(host.get('name') or 'Host')[:100]
            if found is None:
                problems.append({'title': name, 'subtitle': 'Docker host', 'state': 'Host offline'})
                continue
            online += 1
            for container in found:
                if str(container.get('State') or '').lower() == 'running':
                    running += 1
                else:
                    other += 1
                problem = container_problem(container)
                if problem:
                    problems.append({'title': str(container.get('Names') or 'Container')[:250], 'subtitle': name, 'state': problem})
        metrics = [
            {'label': 'Hosts online', 'value': f'{online} of {len(hosts)}'},
            {'label': 'Containers running', 'value': f'{running:,} of {running + other:,}'},
            {'label': 'Not running', 'value': f'{other:,}'},
        ]
        return Result('degraded' if problems else 'healthy',
                      f'{len(problems)} item(s) need attention' if problems else 'HomePod API available', metrics,
                      {'heading': 'Needs attention', 'items': problems[:100], 'empty': 'Every online container is running normally.',
                       'more_label': 'More items'})
