"""Read-only outstanding and filled audiobook requests across visible users."""
import asyncio


class AudioBookRequest:
    id = 'audiobookrequest'
    name = 'AudioBookRequest'
    description = 'Outstanding and filled requests, including manual requests, with requester names and download-error counts.'
    secret_label = 'API key'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'Authorization': 'Bearer ' + secret}
        requests, manual = await asyncio.gather(
            fetch(client, base + '/api/requests', headers),
            fetch(client, base + '/api/requests/manual', headers))
        if not isinstance(requests, list) or not isinstance(manual, list):
            raise ValueError('Expected request lists')
        outstanding = []
        filled = errors = 0
        for entry in requests:
            book = entry['book']
            if not isinstance(book['downloaded'], bool):
                raise ValueError('Expected filled flag')
            if book['downloaded']:
                filled += 1
                continue
            requesters = entry['requests']
            error = bool(entry.get('download_error'))
            errors += error
            outstanding.append({
                'title': str(book['title'])[:250],
                'subtitle': ', '.join(book.get('authors') or [])[:250],
                'user': ', '.join(sorted({r['user_username'] for r in requesters}))[:250],
                'date': max((r.get('updated_at') or '' for r in requesters), default='')[:10],
                'state': 'Download error' if error else '',
            })
        for entry in manual:
            if not isinstance(entry['downloaded'], bool):
                raise ValueError('Expected filled flag')
            if entry['downloaded']:
                filled += 1
                continue
            outstanding.append({
                'title': str(entry['title'])[:250],
                'subtitle': ', '.join(entry.get('authors') or [])[:250],
                'user': str(entry['user_username'])[:250],
                'date': str(entry.get('updated_at') or '')[:10],
                'state': 'Manual request',
            })
        outstanding.sort(key=lambda row: (row['date'], row['title']))
        return Result('degraded' if errors else 'healthy',
                      f'{errors} request(s) with download errors' if errors else 'AudioBookRequest API available', [
            {'label': 'Outstanding requests', 'value': str(len(outstanding))},
            {'label': 'Filled requests', 'value': str(filled)},
            {'label': 'Errors', 'value': str(errors)},
        ], {'heading': 'Outstanding requests', 'items': outstanding,
            'empty': 'No outstanding requests.', 'more_label': 'More requests'})
