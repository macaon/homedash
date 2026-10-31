"""Read-only library totals and reported sessions; online does not mean playing."""
import asyncio
import math
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
    description = 'Library totals, open playback sessions and your most recent listening activity. Open sessions may be paused; offline listening appears after syncing.'
    secret_label = 'API key'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'Authorization': 'Bearer ' + secret}

        async def get(path):
            return await fetch(client, base + path, headers)

        libraries, online = await asyncio.gather(get('/api/libraries'), get('/api/users/online'))
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
        metrics = [
            {'label': 'Open sessions', 'value': str(len(sessions))},
            {'label': 'Library items', 'value': f'{sum(int(number(s["totalItems"])) for s in stats):,}'},
            {'label': 'Libraries', 'value': str(len(libraries))},
        ]
        if sessions:
            # All rows are retained, with excess rows disclosed by the frontend.
            users = {u['id']: u.get('username', '') for u in online.get('usersOnline', [])}
            rows = [activity(s, users.get(s.get('userId'), '')) for s in sessions[:100]]
            details = {'heading': 'Open sessions', 'items': rows,
                       'note': 'Open sessions may be paused. Offline listening appears after syncing.'}
            if len(sessions) > 100:
                details['note'] += ' Showing the first 100 sessions.'
        else:
            recent = await get('/api/me/listening-sessions?itemsPerPage=1&page=0')
            rows = [activity(s) for s in recent['sessions'][:1]]
            details = {'heading': 'Last listened', 'items': rows,
                       'empty': 'No listening history for this API key’s account.'}
        return Result('healthy', 'Audiobookshelf API available', metrics, details)
