"""Public-transport disruptions and planned works from Trafiklab's GTFS Regional ServiceAlerts feeds (needs a free key).

Alerts are published per regional operator, and train events often sit in a neighbouring region's feed,
so a connection can combine up to four regions. The feeds are GTFS-Realtime protobuf; only the handful of
alert fields we show are decoded here, by a small wire-format reader, so no protobuf dependency is needed.
"""
import asyncio
import re
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Operators with a ServiceAlerts feed (GTFS Regional realtime). The value is the label shown in the editor.
REGIONS = {'sl': 'Stockholm (SL)', 'ul': 'Uppsala (UL)', 'otraf': 'Östergötland', 'jlt': 'Jönköping', 'krono': 'Kronoberg',
           'klt': 'Kalmar', 'gotland': 'Gotland', 'blekinge': 'Blekinge', 'skane': 'Skåne', 'varm': 'Värmland',
           'orebro': 'Örebro', 'vastmanland': 'Västmanland', 'dt': 'Dalarna', 'xt': 'Gävleborg (X-trafik)', 'dintur': 'Västernorrland'}
MAX_REGIONS = 4
# Bronze keys allow 30,000 calls a month, so no feed is fetched more than once per 10 minutes, whatever the
# connection interval is (4,320 calls a month per region). Shared by every connection using the same URL.
THROTTLE = 600
CACHE = {}
try:
    LOCAL = ZoneInfo('Europe/Stockholm')
except ZoneInfoNotFoundError:
    LOCAL = None  # dates then show in UTC


def varint(buf, i):
    result = shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7f) << shift
        if byte < 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError('Invalid protobuf')


def fields(buf):
    """Yield (field number, value) for one message: an int for varints, bytes for everything else."""
    i = 0
    try:
        while i < len(buf):
            key, i = varint(buf, i)
            wire = key & 7
            if wire == 0:
                value, i = varint(buf, i)
            elif wire == 2:
                size, i = varint(buf, i)
                value, i = buf[i:i + size], i + size
            elif wire in (1, 5):
                value, i = buf[i:i + (8 if wire == 1 else 4)], i + (8 if wire == 1 else 4)
            else:
                raise ValueError('Unsupported protobuf wire type')
            if i > len(buf):
                raise ValueError('Truncated protobuf')
            yield key >> 3, value
    except IndexError:
        raise ValueError('Truncated protobuf') from None


def text(translated):
    # TranslatedString -> repeated Translation { text = 1, language = 2 }; the feeds send one, without a language.
    return next((t.decode('utf-8', 'replace') for f, v in fields(translated) if f == 1 for g, t in fields(v) if g == 1), '').strip()


def parse_alerts(raw):
    """FeedMessage.entity[].alert -> dicts with start/end epochs (0 = open), route ids, header, description."""
    alerts = []
    for number, entity in fields(raw):
        if number != 2:
            continue
        body = next((v for g, v in fields(entity) if g == 5), None)
        if body is None:
            continue
        alert = {'periods': [], 'routes': [], 'header': '', 'description': ''}
        for g, v in fields(body):
            if g == 1:
                period = dict(fields(v))
                alert['periods'].append((period.get(1, 0), period.get(2, 0)))
            elif g == 5:
                alert['routes'].extend(x.decode() for h, x in fields(v) if h == 2)
            elif g == 10:
                alert['header'] = text(v)
            elif g == 11:
                alert['description'] = text(v)
        alerts.append(alert)
    return alerts


def lines(route_ids):
    # Swedish route ids are 9011 + 3-digit county + 4-digit line + 00000, e.g. 9011007021500000 is line 215.
    found = {int(m[1]) for rid in route_ids if (m := re.fullmatch(r'9011\d{3}(\d{4})\d{5}', rid))}
    return sorted(found)


def matches(alert, terms):
    """Numbers match a line (affected routes, or a whole number in the headline); other terms match the text."""
    if not terms:
        return True
    head, body = alert['header'].lower(), (alert['header'] + ' ' + alert['description']).lower()
    return any((int(t) in lines(alert['routes']) or re.search(rf'(?<!\d){t}(?!\d)', head)) if t.isdigit() else t.lower() in body
               for t in terms)


def day(epoch):
    from datetime import datetime
    when = datetime.fromtimestamp(epoch, LOCAL)
    return f'{when.day} {when.strftime("%b")}'


class Transit:
    id = 'transit'
    name = 'Public transport alerts'
    description = 'Disruptions and planned works for regional buses and trains, from Trafiklab (free API key). Shown in the header.'
    secret_label = 'Trafiklab API key (GTFS Regional Realtime)'
    placement = 'header'
    notice = ('Shows the alerts operators publish in structured form. That can be less than their own websites list, and it can lag behind '
              'what is happening on the ground. Data from Trafiklab.se.')
    default_url = 'https://opendata.samtrafiken.se'
    fields = [{'key': 'regions', 'label': 'Regions', 'options': [{'value': k, 'label': v} for k, v in REGIONS.items()],
               'help': f'Up to {MAX_REGIONS}. Train events are often published by a neighbouring region: for Krösatåg and Alvesta choose Kronoberg, Kalmar and Jönköping.'},
              {'key': 'filter', 'label': 'Only show', 'placeholder': '12, 111, Växjö', 'optional': True,
               'help': 'Optional bus line numbers or words, comma separated. Empty shows everything.'}]

    def validate(self, config):
        regions = list(dict.fromkeys(r.strip() for r in str(config.get('regions', '')).split(',') if r.strip()))
        if not 1 <= len(regions) <= MAX_REGIONS or any(r not in REGIONS for r in regions):
            raise ValueError(f'Choose 1–{MAX_REGIONS} regions')
        terms = [t.strip() for t in str(config.get('filter', '')).split(',') if t.strip()]
        if len(terms) > 8 or any(len(t) > 40 for t in terms):
            raise ValueError('Use at most 8 filter terms of up to 40 characters')
        return {'regions': ', '.join(regions), 'filter': ', '.join(terms)}

    async def feed(self, client, base, region, secret):
        from . import fetch
        url = f'{base}/gtfs-rt/{region}/ServiceAlerts.pb?key={quote(secret)}'
        saved = CACHE.get(url)
        if saved and time.monotonic() - saved[0] < THROTTLE:
            return saved[1]
        # The API refuses clients that do not accept gzip; httpx sends that header and decompresses.
        alerts = parse_alerts(await fetch(client, url, as_json=False, raw=True))
        if len(CACHE) > 64:
            CACHE.clear()
        CACHE[url] = (time.monotonic(), alerts)
        return alerts

    async def poll(self, client, base, config, secret):
        from . import Result
        config = self.validate(config)
        regions = config['regions'].split(', ')
        terms = [t for t in config['filter'].split(', ') if t]
        feeds = await asyncio.gather(*(self.feed(client, base, r, secret) for r in regions), return_exceptions=True)
        if all(isinstance(f, BaseException) for f in feeds):
            raise feeds[0]
        now, seen, rows = time.time(), set(), []
        for alerts in (f for f in feeds if not isinstance(f, BaseException)):
            for alert in alerts:
                key = (alert['header'], alert['description'])
                periods = alert['periods'] or [(0, 0)]
                if (key in seen or max(map(len, key)) < 3 or not matches(alert, terms)
                        or all(end and end < now for _, end in periods)):   # skip repeats, empty "." alerts, filtered and expired ones
                    continue
                seen.add(key)
                active = any(start <= now for start, _ in periods)
                start = min(s for s, _ in periods)
                end = max(e for _, e in periods)
                head = alert['header'] if len(alert['header']) > 2 else alert['description'].split('\n')[0]
                affected = lines(alert['routes'])
                detail = ' '.join(alert['description'].split())
                rows.append((not active, end if active else start, {
                    'title': head[:250], 'state': 'Now' if active else 'Planned',
                    'subtitle': ((f"Line {', '.join(map(str, affected))} · " if affected else '') + detail)[:300],
                    'date': (f'until {day(end)}' if end else '') if active else f'from {day(start)}'}))
        rows.sort(key=lambda r: r[:2])
        active = sum(not r[0] for r in rows)
        planned = len(rows) - active
        failed = sum(isinstance(f, BaseException) for f in feeds)
        return Result('healthy', f'{planned} planned' if planned else '', [
            {'label': 'Active now', 'value': str(active)}, {'label': 'Planned', 'value': str(planned)}], {
            'hero': {'icon': 'triangle-alert' if active else 'circle-check', 'title': f'{active} active' if active else 'All clear',
                     'text': ', '.join(REGIONS[r].split(' (')[0] for r in regions) + (f' · filter: {config["filter"]}' if terms else '')},
            'heading': 'Disruptions and planned works', 'items': [r[2] for r in rows], 'more_label': 'More alerts',
            'empty': 'No disruptions or planned works.',
            **({'note': f'{failed} of {len(regions)} regions could not be fetched.'} if failed else {})})
