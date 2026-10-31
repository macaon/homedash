"""Read-only library totals, disk use and background-job health for Immich."""
import asyncio

DISK_WARNING = 90  # percent used before Needs attention


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected a non-negative integer')
    return value


def size(value):
    value = count(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if value < 1024:
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} TiB'


class Immich:
    id = 'immich'
    name = 'Immich'
    description = 'Photo and video totals, library size, disk use and background-job health. Needs an API key with server.statistics, server.storage and job.read.'
    secret_label = 'API key'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'x-api-key': secret}
        stats, storage, jobs = await asyncio.gather(*(
            fetch(client, base + '/api/' + path, headers)
            for path in ('server/statistics', 'server/storage', 'jobs')))
        percent = storage['diskUsagePercentage']
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
            raise ValueError('Expected disk usage percentage')
        if not isinstance(jobs, dict) or len(jobs) > 64:
            raise ValueError('Expected job queues')
        pending = failed = 0
        for queue in jobs.values():
            counts = queue['jobCounts']
            pending += count(counts['active']) + count(counts['waiting'])
            failed += count(counts['failed'])
        full = percent >= DISK_WARNING
        problems = []
        if failed:
            problems.append(f'{failed} failed job(s)')
        if full:
            problems.append(f'disk {percent:.0f}% full')
        return Result('degraded' if problems else 'healthy',
                      ', '.join(problems).capitalize() if problems else 'Immich API available', [
            {'label': 'Photos', 'value': f'{count(stats["photos"]):,}'},
            {'label': 'Videos', 'value': f'{count(stats["videos"]):,}'},
            {'label': 'Library size', 'value': size(stats['usage'])},
            {'label': 'Disk used', 'value': f'{percent:.0f}% of {size(storage["diskSizeRaw"])}'},
            {'label': 'Jobs pending', 'value': f'{pending:,}'},
            {'label': 'Failed jobs', 'value': f'{failed:,}'},
        ])
