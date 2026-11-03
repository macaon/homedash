"""Next departures and arrivals at a stop, with live delays where the operator provides them, one provider per country.

Sweden: Trafiklab's Realtime APIs (free key). Norway: Entur's Journey Planner (no key).
A provider turns its API into one normalised shape: {'now', 'stop_name', 'calls': [...]} where every call has
line, mode, direction, origin, platform, scheduled / realtime (local, zone-less ISO strings), delay (seconds),
canceled and is_realtime. Everything after that (filtering, headline, lists, throttling) is shared, so another
country only needs a stop search and a board.
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

# Each provider sets how often a list may be fetched: `throttle` for the list the headline follows, `slow` for the other
# one. A failure (such as a 429) is not retried for `throttle` seconds either. The server also stops polling on_demand
# connectors while nobody has the dashboard open. Fetches are shared by every widget using the same stop.
CACHE = {}
ERRORS = {}
KINDS = ('departures', 'arrivals')
SHOW = {'departures': 'Departures', 'arrivals': 'Arrivals', 'next': 'Whichever is next'}
# Normalised vehicle kinds: what the type filter offers, and the icon and label each one gets in the lists.
VEHICLES = {'bus': 'Buses', 'train': 'Trains', 'tram': 'Trams', 'metro': 'Metro', 'boat': 'Boats'}
ICONS = {'bus': 'bus', 'train': 'train-front', 'tram': 'tram-front', 'metro': 'train-front-tunnel', 'boat': 'ship'}
LABELS = {'bus': 'Bus', 'train': 'Train', 'tram': 'Tram', 'metro': 'Metro', 'boat': 'Boat'}
NOTICE = ('Live times come from the transport operators. They are not always available or accurate: many departures show the timetable only, '
          'and live times can be late, early or wrong, in any region. Treat them as a guide, not a guarantee. ')


def when(minutes):
    return 'now' if minutes <= 0 else f'{minutes} min' if minutes < 60 else f'{minutes // 60} h {minutes % 60:02d}'


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


def row(kind, call, minutes):
    label = 'Replacement bus' if call['replacement'] else LABELS.get(call['mode'], 'Bus')
    shown = call['scheduled'][11:16] + (f" → {call['realtime'][11:16]}" if call['is_realtime'] and abs(call['delay']) >= 60 else '')
    return {'title': (f"{call['line']} → {call['direction']}" if kind == 'departures' else f"{call['line']} ← {call['origin'] or call['direction']}")[:250],
            'state': status(call).capitalize(), 'date': shown, 'icon': ICONS.get(call['mode'], 'bus'), 'icon_label': label,
            'subtitle': ('now' if minutes <= 0 else f'in {when(minutes)}') + (f" · platform {call['platform']}" if call['platform'] else '')
                        + (' · replacement bus' if call['replacement'] else '')}


VEHICLE_FIELD = {'key': 'modes', 'label': 'Vehicle types', 'optional': True, 'row': True,
                 'options': [{'value': k, 'label': v} for k, v in VEHICLES.items()],
                 'help': 'Optional. Tick the kinds of vehicle to show, for example only trains. None ticked shows everything. Replacement buses count as buses.'}


class Sweden:
    """Trafiklab Realtime APIs: https://www.trafiklab.se/api/our-apis/trafiklab-realtime-apis/ (Bronze: 25 calls a minute, 100,000 a month)."""
    country_name = 'Sweden'
    secret_label = 'Trafiklab API key (Trafiklab Realtime APIs)'
    default_url = 'https://realtime-api.trafiklab.se'
    throttle, slow = 120, 300
    attribution = 'Data from Trafiklab.se (CC BY 4.0).'
    notice = NOTICE + 'Data from Trafiklab.se.'
    search_ui = {'label': 'Find a stop', 'placeholder': 'Växjö station'}
    fields = [{'key': 'stop', 'label': 'Stop ID', 'placeholder': '740000250', 'help': 'Search for a stop above to fill this in.'},
              {'key': 'stop_name', 'label': 'Stop name', 'optional': True, 'hidden': True},   # filled in by the stop search; the widget's Name is what you rename
              {'key': 'show', 'label': 'Show in the header', 'choices': [{'value': k, 'label': v} for k, v in SHOW.items()],
               'help': 'What the header chip and headline follow. The details always list both departures and arrivals.'},
              {'key': 'lines', 'label': 'Lines', 'placeholder': '9, 14, 123', 'optional': True,
               'help': 'Optional line numbers, comma separated. Empty shows every line at the stop. Only the next 60 minutes are available.'},
              VEHICLE_FIELD]

    MODE = {'BUS': 'bus', 'TRAIN': 'train', 'TRAM': 'tram', 'METRO': 'metro', 'BOAT': 'boat'}

    def validate_stop(self, config):
        stop = str(config.get('stop', '')).strip()
        if not re.fullmatch(r'\d{6,18}', stop):
            raise ValueError('Enter a stop ID, or search for the stop')
        return {'stop': stop}

    async def search(self, client, query, secret, config):
        from . import fetch
        data = await fetch(client, f'{self.default_url}/v1/stops/name/{quote(query, safe="")}?key={quote(secret)}')
        return [{'label': f"{g['name']} · {', '.join(m.title() for m in g.get('transport_modes', []))}",
                 'values': {'stop': str(g['id']), 'stop_name': g['name']}} for g in data.get('stop_groups', [])[:8]]

    async def fetch(self, client, base, kind, stop, secret, config):
        from . import fetch
        data = await fetch(client, f'{base}/v1/{kind}/{stop}?key={quote(secret)}')
        calls = []
        for call in data[kind]:
            route = call['route']
            # 714 is the standard GTFS extended route type for a rail replacement bus; not yet seen in Trafiklab's data.
            replacement = route.get('transport_mode_code') == 714
            calls.append({'line': str(route.get('designation') or route.get('name') or '?'), 'replacement': replacement,
                          'mode': 'bus' if replacement else self.MODE.get(route.get('transport_mode'), ''),
                          'direction': route.get('direction') or (route.get('destination') or {}).get('name') or '',
                          'origin': (route.get('origin') or {}).get('name') or '',
                          'platform': (call.get('realtime_platform') or {}).get('designation') or '',
                          'scheduled': call['scheduled'], 'realtime': call['realtime'], 'delay': call['delay'],
                          'canceled': bool(call['canceled']), 'is_realtime': bool(call['is_realtime'])})
        # The API's times are local and zone-less, and so is its own timestamp.
        return {'now': data['timestamp'], 'stop_name': (data.get('stops') or [{}])[0].get('name', ''), 'calls': calls}

    def headers(self, config):
        return {}


class Norway:
    """Entur Journey Planner v3 (GraphQL) and Geocoder: https://developer.entur.no/apis/open (no key; identify the app with ET-Client-Name)."""
    country_name = 'Norway'
    secret_label = ''
    default_url = 'https://api.entur.io'
    default_client = 'homelab_homedash'
    throttle, slow = 60, 180
    attribution = 'Data from Entur (NLOD).'
    notice = NOTICE + 'Data from Entur.'
    search_ui = {'label': 'Find a stop', 'placeholder': 'Oslo S'}
    fields = [{'key': 'stop', 'label': 'Stop ID', 'placeholder': 'NSR:StopPlace:59872', 'help': 'Search for a stop above to fill this in.'},
              {'key': 'stop_name', 'label': 'Stop name', 'optional': True, 'hidden': True},
              {'key': 'show', 'label': 'Show in the header', 'choices': [{'value': k, 'label': v} for k, v in SHOW.items()],
               'help': 'What the header chip and headline follow. The details always list both departures and arrivals.'},
              {'key': 'lines', 'label': 'Lines', 'placeholder': 'R14, L1, 31', 'optional': True,
               'help': 'Optional line codes, comma separated. Empty shows every line at the stop. Only the next 60 minutes are shown.'},
              VEHICLE_FIELD,
              {'key': 'client_name', 'label': 'Client name', 'placeholder': 'homelab_homedash', 'optional': True,
               'help': 'Entur asks every app to identify itself in the form company-application, lowercase. The default is fine for a home setup.'}]
    MODE = {'rail': 'train', 'bus': 'bus', 'coach': 'bus', 'tram': 'tram', 'metro': 'metro', 'water': 'boat'}
    QUERY = ('query($id:String!){ stopPlace(id:$id){ name estimatedCalls(numberOfDepartures:100, timeRange:3600%s){ realtime cancellation %s '
             'destinationDisplay{frontText} quay{publicCode} serviceJourney{ transportSubmode line{publicCode name transportMode} quays(first:1){ name stopPlace{ name } } } } } }')

    @staticmethod
    def utcnow():
        return datetime.now(timezone.utc)     # a hook, so tests can fix the clock

    def validate_stop(self, config):
        stop = str(config.get('stop', '')).strip()
        if not re.fullmatch(r'NSR:StopPlace:\d{1,12}', stop):
            raise ValueError('Enter a stop ID like NSR:StopPlace:59872, or search for the stop')
        client = str(config.get('client_name') or self.default_client).strip().lower()
        if not re.fullmatch(r'[a-z0-9_-]{3,64}', client):
            raise ValueError('The client name uses lowercase letters, digits, hyphens and underscores')
        return {'stop': stop, 'client_name': client}

    def headers(self, config):
        return {'ET-Client-Name': config.get('client_name') or self.default_client}

    async def search(self, client, query, secret, config):
        from . import fetch
        client_name = str(config.get('client_name') or self.default_client).strip().lower()
        data = await fetch(client, f'{self.default_url}/geocoder/v3/autocomplete?' + urlencode({'q': query, 'layers': 'stopPlace', 'limit': 8, 'lang': 'no'}),
                           {'ET-Client-Name': client_name})
        return [{'label': p['names'].get('display') or p['names']['default'], 'values': {'stop': p['id'], 'stop_name': p['names'].get('default', '')}}
                for p in (f['properties'] for f in data.get('features', []))]

    async def fetch(self, client, base, kind, stop, secret, config):
        from . import fetch
        arrivals = kind == 'arrivals'
        query = self.QUERY % (', arrivalDeparture: arrivals' if arrivals else '', 'aimedArrivalTime expectedArrivalTime' if arrivals else 'aimedDepartureTime expectedDepartureTime')
        data = await fetch(client, f'{base}/journey-planner/v3/graphql', self.headers(config), json={'query': query, 'variables': {'id': stop}})
        place = (data.get('data') or {}).get('stopPlace')
        if not place:
            raise ValueError('Unknown stop')
        offset, calls = None, []
        for call in place['estimatedCalls']:
            aimed = datetime.fromisoformat(call['aimedArrivalTime' if arrivals else 'aimedDepartureTime'])
            expected = datetime.fromisoformat(call['expectedArrivalTime' if arrivals else 'expectedDepartureTime'])
            offset = aimed.utcoffset()
            journey = call['serviceJourney']
            line, origin = journey['line'], (journey.get('quays') or [{}])[0]
            # Entur calls a replacement bus's line "rail"; only the submode says it is a bus.
            replacement = journey.get('transportSubmode') == 'railReplacementBus'
            calls.append({'line': str(line.get('publicCode') or line.get('name') or '?'), 'replacement': replacement,
                          'mode': 'bus' if replacement else self.MODE.get(line.get('transportMode'), ''),
                          'direction': (call.get('destinationDisplay') or {}).get('frontText') or '',
                          'origin': (origin.get('stopPlace') or {}).get('name') or origin.get('name') or '',
                          'platform': (call.get('quay') or {}).get('publicCode') or '',
                          'scheduled': aimed.replace(tzinfo=None).isoformat(timespec='seconds'), 'realtime': expected.replace(tzinfo=None).isoformat(timespec='seconds'),
                          'delay': round((expected - aimed).total_seconds()), 'canceled': bool(call['cancellation']), 'is_realtime': bool(call['realtime'])})
        # Times carry their UTC offset, which is Norwegian local time; "now" in that frame needs no timezone database.
        now = (self.utcnow() + (offset or timedelta(0))).replace(tzinfo=None)
        return {'now': now.isoformat(timespec='seconds'), 'stop_name': place.get('name') or '', 'calls': calls}


class Departures:
    id = 'departures'    # kept from when this only listed departures in Sweden, so saved widgets keep working
    name = 'Public transport times'
    description = 'Departures and arrivals at a stop (Sweden and Norway), for all lines or only the ones you pick, with live delays where available. Shown in the header.'
    secret_label = ''    # per country: see the providers
    fields = []
    placement = 'header'
    on_demand = True
    providers = {'se': Sweden(), 'no': Norway()}
    default_country = 'se'

    def provider(self, config):
        country = str(config.get('country') or self.default_country)
        if country not in self.providers:
            raise ValueError('Choose a country')
        return self.providers[country]

    def validate(self, config):
        country = str(config.get('country') or self.default_country)
        provider = self.provider(config)
        lines = list(dict.fromkeys(t.strip() for t in str(config.get('lines', '')).split(',') if t.strip()))
        if len(lines) > 6 or any(not re.fullmatch(r'[\w ]{1,10}', t) for t in lines):
            raise ValueError('Enter up to 6 line numbers, comma separated')
        modes = list(dict.fromkeys(t.strip() for t in str(config.get('modes', '')).split(',') if t.strip()))
        if any(m not in VEHICLES for m in modes):
            raise ValueError('Choose buses, trains, trams, metro or boats')
        show = str(config.get('show') or 'departures')
        if show not in SHOW:
            raise ValueError('Choose departures, arrivals or whichever is next')
        return {'country': country, **provider.validate_stop(config), 'stop_name': str(config.get('stop_name', '')).strip()[:100], 'show': show,
                'lines': ', '.join(lines), 'modes': ', '.join(m for m in VEHICLES if m in modes)}

    async def board(self, provider, client, base, config, secret, kind, ttl):
        key = (provider.country_name, base, config['stop'], kind, secret)
        now = time.monotonic()
        if key in ERRORS and now - ERRORS[key][0] < provider.throttle:
            raise ERRORS[key][1]
        if key in CACHE and now - CACHE[key][0] < ttl:
            return CACHE[key]
        try:
            data = await provider.fetch(client, base, kind, config['stop'], secret, config)
        except Exception as error:
            ERRORS[key] = (now, error)
            raise
        ERRORS.pop(key, None)
        if len(CACHE) > 64:
            CACHE.clear()
        CACHE[key] = (now, data)
        return CACHE[key]

    async def poll(self, client, base, config, secret):
        from . import Result
        config = self.validate(config)
        provider = self.provider(config)
        show, wanted, modes = config['show'], [t for t in config['lines'].split(', ') if t], [m for m in config['modes'].split(', ') if m]
        needed = KINDS if show == 'next' else (show,)
        fetched = dict(zip(KINDS, await asyncio.gather(
            *(self.board(provider, client, base, config, secret, k, provider.throttle if k in needed else provider.slow) for k in KINDS), return_exceptions=True)))
        for kind in needed:
            if isinstance(fetched[kind], BaseException):
                raise fetched[kind]
        calls, place = {}, config['stop_name']
        for kind, result in fetched.items():
            if isinstance(result, BaseException):
                continue          # the list the headline does not need may be missing
            stamp, data = result
            now = datetime.fromisoformat(data['now']) + timedelta(seconds=time.monotonic() - stamp)    # advance by the age of our copy
            place = place or data['stop_name']
            found = []
            for call in data['calls']:
                leaves = datetime.fromisoformat(call['realtime'])
                if ((wanted and call['line'].lower() not in (w.lower() for w in wanted)) or (modes and call['mode'] not in modes)
                        or leaves < now - timedelta(minutes=1)):
                    continue
                found.append((leaves, kind, call, max(0, round((leaves - now).total_seconds() / 60))))
            calls[kind] = sorted(found, key=lambda c: c[0])
        primary = sorted((c for k in needed for c in calls.get(k, [])), key=lambda c: c[0])
        metrics = []
        for w in wanted:        # the next one for each line you follow, in what the headline follows
            nxt = next((c for c in primary if c[2]['line'].lower() == w.lower()), None)
            tag = f"{'dep' if nxt[1] == 'departures' else 'arr'} · " if nxt and show == 'next' else ''
            metrics.append({'label': f'Line {w}', 'value': when(nxt[3]) if nxt else '—', 'unit': tag + status(nxt[2]) if nxt else 'none within the hour'})
        if primary:
            leaves, kind, call, minutes = primary[0]
            hero = {'icon': ICONS.get(call['mode'], 'bus'), 'title': headline(call['line'], kind, show, minutes),
                    'text': ((f"{call['direction']} · {place}" if kind == 'departures' else f"from {call['origin'] or call['direction']} · {place}")
                             + (' · replacement bus' if call['replacement'] else '')).strip(' ·')}
            summary = status(call)
        else:
            hero = {'icon': 'bus', 'title': 'Nothing due', 'text': f'in the next hour · {place}'.strip(' ·')}
            summary = ''
        lists = [{'heading': SHOW[k], 'empty': f'No matching {k} in the next hour.' if k in calls else f'{SHOW[k]} could not be fetched.',
                  'items': [row(k, c[2], c[3]) for c in calls.get(k, [])[:5]]} for k in sorted(KINDS, key=lambda k: k != show)]
        return Result('healthy', summary, metrics, {
            'hero': hero, 'lists': lists, 'note': f'Live times are not always available or accurate. {provider.attribution}'})
