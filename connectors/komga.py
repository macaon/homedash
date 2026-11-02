"""Read-only series and book totals, reading progress and library health for Komga."""
import asyncio

BOOK_COUNTS = (('books', ''), ('unread', 'read_status=UNREAD'), ('progress', 'read_status=IN_PROGRESS'),
               ('unreadable', 'media_status=ERROR'), ('unsupported', 'media_status=UNSUPPORTED'))


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected a non-negative integer')
    return value


class Komga:
    id = 'komga'
    name = 'Komga'
    description = 'Series and book totals, reading progress and library health. Needs an API key; it sees what that user is allowed to see.'
    secret_label = 'API key'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'X-API-Key': secret}
        # One-item pages: the response carries the total, and no more than one book or series is read.
        urls = [base + '/api/v1/libraries', base + '/api/v1/series?size=1'] + [
            base + '/api/v1/books?size=1' + ('&' + query if query else '') for _, query in BOOK_COUNTS]
        libraries, series, *books = await asyncio.gather(*(fetch(client, url, headers) for url in urls))
        if not isinstance(libraries, list) or len(libraries) > 1000:
            raise ValueError('Expected a library list')
        totals = {key: count(page['totalElements']) for (key, _), page in zip(BOOK_COUNTS, books)}
        missing = [library['name'] for library in libraries if library.get('unavailable') is True]
        problem = f'{len(missing)} library(s) unavailable' if missing else ''
        return Result('degraded' if problem else 'healthy', problem.capitalize() if problem else 'Komga API available', [
            {'label': 'Series', 'value': f'{count(series["totalElements"]):,}'},
            {'label': 'Books', 'value': f'{totals["books"]:,}'},
            {'label': 'Unread', 'value': f'{totals["unread"]:,}'},
            {'label': 'In progress', 'value': f'{totals["progress"]:,}'},
        ], {'sections': [{'heading': 'Library health', 'metrics': [
            {'label': 'Libraries', 'value': f'{len(libraries):,}'},
            {'label': 'Unavailable', 'value': ', '.join(str(name)[:60] for name in missing) or 'None'},
            {'label': 'Unreadable books', 'value': f'{totals["unreadable"]:,}'},
            {'label': 'Unsupported books', 'value': f'{totals["unsupported"]:,}'},
        ]}]})
