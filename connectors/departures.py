"""Next departures and arrivals at a stop, with live delays where the operator provides them (Trafiklab Realtime APIs, free key)."""
import asyncio
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

# Bronze keys allow 25 calls a minute and 100,000 a month. A list is fetched at most every two minutes however short the
# connection interval is, shared by every widget using the stop, and a failure (such as a 429) is not retried for as long.
# The list the headline does not need is only refreshed every five minutes, and the server also stops polling on_demand
# connectors while nobody has the dashboard open.
THROTTLE = 120
SLOW = 300
CACHE = {}
ERRORS = {}
KINDS = ('departures', 'arrivals')
SHOW = {'departures': 'Departures', 'arrivals': 'Arrivals', 'next': 'Whichever is next'}
MODES = {'TRAIN': 'train-front', 'TRAM': 'tram-front', 'METRO': 'train-front-tunnel', 'BOAT': 'ship'}


def when(minutes):
    return 'now' if minutes <= 0 else f'{minutes} min' if minutes < 60 else f'{minutes // 60} h {minutes % 60:02d}'


def direction(call):
    route = call['route']
    return route.get('direction') or (route.get('destination') or {}).get('name') or ''


def origin(call):
    route = call['route']
    return (route.get('origin') or {}).get('name') or direction(call)


def status(call):
    if call['canceled']:
        return 'cancelled'
    if not call['is_realtime']:
        return 'scheduled'     # no live data: never claim "on time"
    delay = round(call['delay'] / 60)
    return 'on time' if delay == 0 else f'{delay:+d} min'


def headline(line, kind, show, minutes):
    verb = '' if show == 'departures' else ' departs' if kind == 'departures' else ' arrives'
    return f'{line}{verb} now' if minutes <= 0 else f'{line}{verb} in {when(minutes)}'


def row(kind, line, call, minutes):
    shown = call['scheduled'][11:16] + (f" → {call['realtime'][11:16]}" if call['is_realtime'] and abs(call['delay']) >= 60 else '')
    platform = (call.get('realtime_platform') or {}).get('designation')
    return {'title': (f'{line} → {direction(call)}' if kind == 'departures' else f'{line} ← {origin(call)}')[:250],
            'state': status(call).capitalize(), 'date': shown,
            'subtitle': ('now' if minutes <= 0 else f'in {when(minutes)}') + (f' · platform {platform}' if platform else '')}


class Departures:
    id = 'departures'    # kept from when this only listed departures, so saved widgets keep working
    name = 'Public transport times'
    description = 'Departures and arrivals at a stop, for all lines or only the ones you pick, with live delays where available. Shown in the header.'
    secret_label = 'Trafiklab API key (Trafiklab Realtime APIs)'
    placement = 'header'
    on_demand = True
    default_url = 'https://realtime-api.trafiklab.se'
    notice = ('Live times come from the transport operators. They are not always available or accurate: many departures show the timetable only, '
              'and live times can be late, early or wrong, in any region. Treat them as a guide, not a guarantee. Data from Trafiklab.se.')
    search_ui = {'label': 'Find a stop', 'placeholder': 'Växjö station'}
    fields = [{'key': 'stop', 'label': 'Stop ID', 'placeholder': '740000250', 'help': 'Search for a stop above to fill this in.'},
              {'key': 'stop_name', 'label': 'Stop name', 'optional': True, 'hidden': True},   # filled in by the stop search; the widget's Name is what you rename
              {'key': 'show', 'label': 'Show in the header', 'choices': [{'value': k, 'label': v} for k, v in SHOW.items()],
               'help': 'What the header chip and headline follow. The details always list both departures and arrivals.'},
              {'key': 'lines', 'label': 'Lines', 'placeholder': '9, 14, 123', 'optional': True,
               'help': 'Optional line numbers, comma separated. Empty shows every line at the stop. Only the next 60 minutes are available.'}]

    def validate(self, config):
        stop = str(config.get('stop', '')).strip()
        if not re.fullmatch(r'\d{6,18}', stop):
            raise ValueError('Enter a stop ID, or search for the stop')
        lines = list(dict.fromkeys(t.strip() for t in str(config.get('lines', '')).split(',') if t.strip()))
        if len(lines) > 6 or any(not re.fullmatch(r'[\w ]{1,10}', t) for t in lines):
            raise ValueError('Enter up to 6 line numbers, comma separated')
        show = str(config.get('show') or 'departures')
        if show not in SHOW:
            raise ValueError('Choose departures, arrivals or whichever is next')
        return {'stop': stop, 'stop_name': str(config.get('stop_name', '')).strip()[:100], 'show': show, 'lines': ', '.join(lines)}

    async def search(self, client, query, secret):
        from . import fetch
        data = await fetch(client, f'{self.default_url}/v1/stops/name/{quote(query, safe="")}?key={quote(secret)}')
        return [{'label': f"{g['name']} · {', '.join(m.title() for m in g.get('transport_modes', []))}",
                 'values': {'stop': str(g['id']), 'stop_name': g['name']}} for g in data.get('stop_groups', [])[:8]]

    async def board(self, client, base, kind, stop, secret, ttl):
        from . import fetch
        url = f'{base}/v1/{kind}/{stop}?key={quote(secret)}'
        now = time.monotonic()
        if url in ERRORS and now - ERRORS[url][0] < THROTTLE:
            raise ERRORS[url][1]
        if url in CACHE and now - CACHE[url][0] < ttl:
            return CACHE[url]
        try:
            data = await fetch(client, url)
        except Exception as error:
            ERRORS[url] = (now, error)
            raise
        ERRORS.pop(url, None)
        if len(CACHE) > 64:
            CACHE.clear()
        CACHE[url] = (now, data)
        return CACHE[url]

    async def poll(self, client, base, config, secret):
        from . import Result
        config = self.validate(config)
        show, wanted = config['show'], [t for t in config['lines'].split(', ') if t]
        needed = KINDS if show == 'next' else (show,)
        fetched = dict(zip(KINDS, await asyncio.gather(
            *(self.board(client, base, k, config['stop'], secret, THROTTLE if k in needed else SLOW) for k in KINDS), return_exceptions=True)))
        for kind in needed:
            if isinstance(fetched[kind], BaseException):
                raise fetched[kind]
        calls, place = {}, config['stop_name']
        for kind, result in fetched.items():
            if isinstance(result, BaseException):
                continue          # the list the headline does not need may be missing
            stamp, data = result
            # The API's times are local and zone-less; its own timestamp, advanced by the age of our copy, is "now".
            now = datetime.fromisoformat(data['timestamp']) + timedelta(seconds=time.monotonic() - stamp)
            place = place or (data.get('stops') or [{}])[0].get('name', '')
            found = []
            for call in data[kind]:
                route = call['route']
                line = str(route.get('designation') or route.get('name') or '?')
                leaves = datetime.fromisoformat(call['realtime'])
                if (wanted and line.lower() not in (w.lower() for w in wanted)) or leaves < now - timedelta(minutes=1):
                    continue
                found.append((leaves, kind, line, call, max(0, round((leaves - now).total_seconds() / 60))))
            calls[kind] = sorted(found, key=lambda c: c[0])
        primary = sorted((c for k in needed for c in calls.get(k, [])), key=lambda c: c[0])
        metrics = []
        for w in wanted:        # the next one for each line you follow, in what the headline follows
            nxt = next((c for c in primary if c[2].lower() == w.lower()), None)
            tag = f"{'dep' if nxt[1] == 'departures' else 'arr'} · " if nxt and show == 'next' else ''
            metrics.append({'label': f'Line {w}', 'value': when(nxt[4]) if nxt else '—', 'unit': tag + status(nxt[3]) if nxt else 'none within the hour'})
        if primary:
            leaves, kind, line, call, minutes = primary[0]
            hero = {'icon': MODES.get(call['route'].get('transport_mode'), 'bus'), 'title': headline(line, kind, show, minutes),
                    'text': (f'{direction(call)} · {place}' if kind == 'departures' else f'from {origin(call)} · {place}').strip(' ·')}
            summary = status(call)
        else:
            hero = {'icon': 'bus', 'title': 'Nothing due', 'text': f'in the next hour · {place}'.strip(' ·')}
            summary = ''
        lists = [{'heading': SHOW[k], 'empty': f'No matching {k} in the next hour.' if k in calls else f'{SHOW[k]} could not be fetched.',
                  'items': [row(k, c[2], c[3], c[4]) for c in calls.get(k, [])[:5]]} for k in sorted(KINDS, key=lambda k: k != show)]
        return Result('healthy', summary, metrics, {
            'hero': hero, 'lists': lists, 'note': 'Live times are not always available or accurate. Data from Trafiklab.se (CC BY 4.0).'})
