"""Read-only document totals, inbox, disk use and import health for Paperless-ngx."""
import httpx

DISK_WARNING = 90  # percent used before Needs attention
# The classifier is left out on purpose: Paperless replaces its hourly training task whenever documents arrive,
# which briefly reports ERROR although nothing is wrong, and it only suggests tags.
TASK_COMPONENTS = (('redis', 'Redis'), ('celery', 'Workers'), ('index', 'Search index'), ('sanity_check', 'Sanity check'))


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected a non-negative integer')
    return value


def size(value):
    value = count(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


class Paperless:
    id = 'paperless'
    name = 'Paperless-ngx'
    description = 'Document and inbox totals, disk use and import health. Needs an API token; an administrator token also shows system health.'
    secret_label = 'API token'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'Authorization': f'Token {secret}'}
        stats = await fetch(client, base + '/api/statistics/', headers)
        try:  # system status is administrator-only; the card works without it
            status = await fetch(client, base + '/api/status/', headers)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 403:
                raise
            status = None
        metrics = [{'label': 'Documents', 'value': f'{count(stats["documents_total"]):,}'},
                   {'label': 'Inbox', 'value': f'{count(stats["documents_inbox"]):,}'}]
        problems, health = [], []
        if status is not None:
            total, free = count(status['storage']['total']), count(status['storage']['available'])
            percent = 100 * (total - free) / total if total else 0
            metrics.append({'label': 'Disk used', 'value': f'{percent:.0f}% of {size(total)}'})
            if percent >= DISK_WARNING:
                problems.append(f'disk {percent:.0f}% full')
            tasks = status.get('tasks') or {}
            failed = count((tasks.get('summary') or {}).get('failure_count', 0))
            broken = [label for key, label in TASK_COMPONENTS if tasks.get(key + '_status') == 'ERROR']
            if (status.get('database') or {}).get('status') == 'ERROR':
                broken.insert(0, 'Database')
            if failed:
                problems.append(f'{failed} failed import(s)')
            if broken:
                problems.append(', '.join(broken) + ' failing')
            health = [{'label': 'Failed imports (30 days)', 'value': f'{failed:,}'},
                      {'label': 'Components failing', 'value': ', '.join(broken) or 'None'}]
        details = {'sections': [{'heading': 'Health', 'metrics': health}]} if health else {}
        summary = ', '.join(problems)
        return Result('degraded' if problems else 'healthy',
                      summary[:1].upper() + summary[1:] if problems else 'Paperless API available', metrics, details)
