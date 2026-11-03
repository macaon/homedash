"""Next departures at a stop, with live delays where the operator provides them (Trafiklab Realtime APIs, free key)."""
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

# Bronze keys allow 25 calls a minute and 100,000 a month. A stop is fetched at most every two minutes however short the
# connection interval is, shared by every widget using it, and a failure (such as a 429) is not retried for as long.
# The server also stops polling on_demand connectors while nobody has the dashboard open.
THROTTLE = 120
CACHE = {}
ERRORS = {}
MODES = {'TRAIN': 'train-front', 'TRAM': 'tram-front', 'METRO': 'train-front-tunnel', 'BOAT': 'ship'}


def when(minutes):
    return 'now' if minutes <= 0 else f'{minutes} min' if minutes < 60 else f'{minutes // 60} h {minutes % 60:02d}'


def direction(call):
    route = call['route']
    return route.get('direction') or (route.get('destination') or {}).get('name') or ''


def status(call):
    if call['canceled']:
        return 'cancelled'
    if not call['is_realtime']:
        return 'scheduled'     # no live data: never claim "on time"
    delay = round(call['delay'] / 60)
    return 'on time' if delay == 0 else f'{delay:+d} min'


class Departures:
    id = 'departures'
    name = 'Public transport departures'
    description = 'The next departures at a stop, for all lines or only the ones you pick, with live delays where available. Shown in the header.'
    secret_label = 'Trafiklab API key (Trafiklab Realtime APIs)'
    placement = 'header'
    on_demand = True
    default_url = 'https://realtime-api.trafiklab.se'
    notice = ('Live times come from the transport operators. They are not always available or accurate: many departures show the timetable only, '
              'and live times can be late, early or wrong, in any region. Treat them as a guide, not a guarantee. Data from Trafiklab.se.')
    search_ui = {'label': 'Find a stop', 'placeholder': 'Växjö station'}
    fields = [{'key': 'stop', 'label': 'Stop ID', 'placeholder': '740000250', 'help': 'Search for a stop above to fill this in.'},
              {'key': 'stop_name', 'label': 'Stop name', 'optional': True, 'hidden': True},   # filled in by the stop search; the widget's Name is what you rename
              {'key': 'lines', 'label': 'Lines', 'placeholder': '9, 14, 123', 'optional': True,
               'help': 'Optional line numbers, comma separated. Empty shows every line at the stop. Only the next 60 minutes are available.'}]

    def validate(self, config):
        stop = str(config.get('stop', '')).strip()
        if not re.fullmatch(r'\d{6,18}', stop):
            raise ValueError('Enter a stop ID, or search for the stop')
        lines = list(dict.fromkeys(t.strip() for t in str(config.get('lines', '')).split(',') if t.strip()))
        if len(lines) > 6 or any(not re.fullmatch(r'[\w ]{1,10}', t) for t in lines):
            raise ValueError('Enter up to 6 line numbers, comma separated')
        return {'stop': stop, 'stop_name': str(config.get('stop_name', '')).strip()[:100], 'lines': ', '.join(lines)}

    async def search(self, client, query, secret):
        from . import fetch
        data = await fetch(client, f'{self.default_url}/v1/stops/name/{quote(query, safe="")}?key={quote(secret)}')
        return [{'label': f"{g['name']} · {', '.join(m.title() for m in g.get('transport_modes', []))}",
                 'values': {'stop': str(g['id']), 'stop_name': g['name']}} for g in data.get('stop_groups', [])[:8]]

    async def board(self, client, base, stop, secret):
        from . import fetch
        url = f'{base}/v1/departures/{stop}?key={quote(secret)}'
        now = time.monotonic()
        if url in ERRORS and now - ERRORS[url][0] < THROTTLE:
            raise ERRORS[url][1]
        if url in CACHE and now - CACHE[url][0] < THROTTLE:
            return CACHE[url]
        try:
            data = await fetch(client, url)
        except Exception as error:
            ERRORS[url] = (now, error)
            raise
        ERRORS.pop(url, None)
        if len(CACHE) > 32:
            CACHE.clear()
        CACHE[url] = (now, data)
        return CACHE[url]

    async def poll(self, client, base, config, secret):
        from . import Result
        config = self.validate(config)
        fetched, data = await self.board(client, base, config['stop'], secret)
        # The API's times are local and zone-less; its own timestamp, advanced by the age of our copy, is "now".
        now = datetime.fromisoformat(data['timestamp']) + timedelta(seconds=time.monotonic() - fetched)
        wanted = [t for t in config['lines'].split(', ') if t]
        calls = []
        for call in data['departures']:
            route = call['route']
            line = str(route.get('designation') or route.get('name') or '?')
            leaves = datetime.fromisoformat(call['realtime'])
            if (wanted and line.lower() not in (w.lower() for w in wanted)) or leaves < now - timedelta(minutes=1):
                continue
            calls.append((leaves, line, call, max(0, round((leaves - now).total_seconds() / 60))))
        calls.sort(key=lambda c: c[0])
        place = config['stop_name'] or (data.get('stops') or [{}])[0].get('name', '')
        rows = []
        for leaves, line, call, minutes in calls[:12]:
            shown = call['scheduled'][11:16] + (f" → {call['realtime'][11:16]}" if call['is_realtime'] and abs(call['delay']) >= 60 else '')
            rows.append({'title': f"{line} → {direction(call)}"[:250],
                         'state': status(call).capitalize(), 'date': shown,
                         'subtitle': f"in {when(minutes)}" + (f" · platform {call['realtime_platform']['designation']}" if call.get('realtime_platform') and call['realtime_platform'].get('designation') else '')})
        metrics = []
        for w in wanted:        # the next departure of each line you follow
            nxt = next((c for c in calls if c[1].lower() == w.lower()), None)
            metrics.append({'label': f'Line {w}', 'value': when(nxt[3]) if nxt else '—', 'unit': status(nxt[2]) if nxt else 'none within the hour'})
        if calls:
            leaves, line, call, minutes = calls[0]
            icon = MODES.get(call['route'].get('transport_mode'), 'bus')
            hero = {'icon': icon, 'title': f'{line} in {when(minutes)}' if minutes else f'{line} now',
                    'text': f"{direction(call)} · {place}".strip(' ·')}
            summary = status(call)
        else:
            hero = {'icon': 'bus', 'title': 'No departures', 'text': f'in the next hour · {place}'.strip(' ·')}
            summary = ''
        return Result('healthy', summary, metrics, {
            'hero': hero, 'heading': 'Next departures', 'items': rows, 'more_label': 'More departures',
            'empty': 'No matching departures in the next hour.',
            'note': 'Live times are not always available or accurate. Data from Trafiklab.se (CC BY 4.0).'})
