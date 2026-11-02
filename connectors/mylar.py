"""Read-only series, issue and wanted totals for Mylar3. The API takes its key only as a query parameter and
answers HTTP 200 even for errors, so every reply is checked for success. History and logs are never read:
they are unbounded and can exceed the response limit."""
import asyncio
from urllib.parse import urlencode

SERIES_STATES = ('Active', 'Paused', 'Ended')


def rows(reply, key=None):
    if isinstance(reply, dict):
        if reply.get('success') is False:
            raise ValueError('Mylar rejected the request: ' + str((reply.get('error') or {}).get('message', ''))[:100])
        reply = reply[key] if key and key in reply else reply.get('data')
    if not isinstance(reply, list) or len(reply) > 100000:
        raise ValueError('Expected a list')
    return reply


class Mylar:
    id = 'mylar'
    name = 'Mylar3'
    description = 'Series, tracked issue, wanted and upcoming totals. Needs the API key (Mylar3: Configuration → Web Interface → API). The key travels in the request URL, so use a direct internal address where possible.'
    secret_label = 'API key'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        def call(cmd):
            return fetch(client, f'{base}/api?{urlencode({"apikey": secret, "cmd": cmd})}')
        index, wanted, upcoming = await asyncio.gather(call('getIndex'), call('getWanted'), call('getUpcoming'))
        series, wanted, upcoming = rows(index), rows(wanted, 'issues'), rows(upcoming)
        states = {state: sum(item.get('status') == state for item in series) for state in SERIES_STATES}
        issues = sum(int(item.get('totalIssues') or 0) for item in series)
        return Result('healthy', 'Mylar API available', [
            {'label': 'Series', 'value': f'{len(series):,}'},
            {'label': 'Issues', 'value': f'{issues:,}'},
            {'label': 'Wanted', 'value': f'{len(wanted):,}'},
            {'label': 'Upcoming', 'value': f'{len(upcoming):,}'},
        ], {'sections': [{'heading': 'Series', 'metrics': [{'label': state, 'value': f'{count:,}'} for state, count in states.items()]}]})
