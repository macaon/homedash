"""Read-only library totals and reported sessions; online does not mean playing."""
import asyncio
import math
import time
from urllib.parse import quote


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('Expected a finite number')
    return max(0, value)


def activity(session, username=''):
    duration = number(session.get('duration') or 0)
    position = number(session.get('currentTime') or 0)
    user = session.get('user') or {}
    row = {
        'title': str(session.get('displayTitle') or 'Untitled')[:250],
        'subtitle': str(session.get('displayAuthor') or '')[:250],
        'user': str(user.get('username') or username)[:100],
        'date': str(session.get('date') or '')[:10],
    }
    if duration:
        row['progress'] = round(min(100, position / duration * 100), 1)
        row['remaining_minutes'] = math.ceil(max(0, duration - position) / 60)
    return row


class Audiobookshelf:
    id = 'audiobookshelf'
    name = 'Audiobookshelf'
    description = 'Library totals, open sessions and recently synced listening activity across users. Session activity does not prove playback is still running.'
    secret_label = 'API key'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'Authorization': 'Bearer ' + secret}

        async def get(path):
            return await fetch(client, base + path, headers)

        libraries, online, recent = await asyncio.gather(
            get('/api/libraries'), get('/api/users/online'),
            get('/api/sessions?itemsPerPage=100&desc=1'))
        libraries = libraries['libraries']
        # Bound fan-out and total work within the scheduler's existing timeout.
        if len(libraries) > 32:
            raise ValueError('Too many libraries')
        stats = []
        for offset in range(0, len(libraries), 4):
            stats.extend(await asyncio.gather(*(
                get('/api/libraries/' + quote(str(lib['id']), safe='') + '/stats')
                for lib in libraries[offset:offset + 4])))
        sessions = online['openSessions']
        users = {u['id']: u.get('username', '') for u in online.get('usersOnline', [])}
        rows = []
        seen_users = set()
        for session in sessions[:100]:
            row = activity(session, users.get(session.get('userId'), ''))
            row['state'] = 'Open session'
            rows.append(row)
            if session.get('userId'):
                seen_users.add(session['userId'])
        # Some clients sync sessions without opening a server-side playback session.
        # A short recency window is an activity signal, not a playing/paused flag.
        now = time.time()
        for session in sorted(recent['sessions'], key=lambda s: number(s.get('updatedAt') or 0), reverse=True):
            updated = number(session.get('updatedAt') or 0) / 1000
            user_id = session.get('userId')
            if not 0 <= now - updated <= 180 or not user_id or user_id in seen_users:
                continue
            row = activity(session, users.get(user_id, ''))
            row['state'] = 'Recently synced'
            rows.append(row)
            seen_users.add(user_id)
        metrics = [
            {'label': 'Sessions', 'value': str(len(rows))},
            {'label': 'Library items', 'value': f'{sum(int(number(s["totalItems"])) for s in stats):,}'},
            {'label': 'Libraries', 'value': str(len(libraries))},
        ]
        details = {'heading': 'Recent activity', 'items': rows,
                   'empty': 'No open or recently synced sessions.'}
        if len(sessions) > 100:
            details['note'] = 'Showing the first 100 open sessions.'
        return Result('healthy', 'Audiobookshelf API available', metrics, details)
