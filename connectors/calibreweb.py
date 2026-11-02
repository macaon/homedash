"""Read-only book total and newest book for Calibre-Web. It has no API, so this signs in like a browser
and reads the Books list table, which reports its total and lists the newest book first; one row is requested."""
import re
from datetime import datetime


class CalibreWeb:
    id = 'calibreweb'
    name = 'Calibre-Web'
    description = 'Book total. Calibre-Web has no API tokens, so this signs in as a user with the Books list permission; use a dedicated read-only user.'
    secret_label = 'Password'
    fields = [{'key': 'username', 'label': 'Username', 'placeholder': 'homedash',
               'help': 'A dedicated user without admin, upload or edit rights, but with the "Show books list" permission.'}]

    def validate(self, config):
        username = str(config.get('username', '')).strip()
        if not 1 <= len(username) <= 100:
            raise ValueError('Enter the Calibre-Web username')
        return {'username': username}

    async def poll(self, client, base, config, secret):
        page = await client.get(base + '/login')
        page.raise_for_status()
        token = re.search(r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', page.text)
        if not token:
            raise ValueError('This does not look like Calibre-Web')
        # A successful sign-in redirects; a rejected one shows the form again with status 200.
        login = await client.post(base + '/login', data={'username': config['username'], 'password': secret,
                                                          'csrf_token': token[1], 'next': '/', 'remember_me': ''})
        if login.status_code == 429:
            raise ValueError('Calibre-Web is limiting sign-ins for this user; wait a few minutes')
        if login.status_code != 302:
            raise ValueError('Calibre-Web rejected the username or password')
        books = await client.get(base + '/ajax/listbooks', params={'limit': 1, 'offset': 0})
        if books.status_code == 302:
            raise ValueError('The user needs the "Show books list" permission')
        books.raise_for_status()
        total = books.json()['totalNotFiltered']
        if type(total) is not int or total < 0:
            raise ValueError('Expected a book total')
        rows = []
        for row in books.json()['rows'][:1]:
            try:
                added = datetime.fromisoformat(row['atom_timestamp']).date().isoformat()
            except (KeyError, TypeError, ValueError):
                added = ''  # a version without the field still shows the book
            rows.append({'title': str(row['title'])[:250], 'subtitle': str(row.get('authors') or '')[:250], 'date': added})
        from . import Result
        return Result('healthy', 'Calibre-Web available', [{'label': 'Books', 'value': f'{total:,}'}],
                      {'heading': 'Last added book', 'items': rows, 'empty': 'No books in the library.'})
