import asyncio
import time
import unittest
from pathlib import Path
import httpx
from connectors import REGISTRY, manifest
from connectors import transit
from connectors.transit import lines, matches, parse_alerts

# A real Kronoberg ServiceAlerts response (2026-11-03): five bus alerts.
FIXTURE = (Path(__file__).parent / 'fixtures' / 'krono-alerts.pb').read_bytes()
# Five real Entur alerts (2026-11-03): two RUT ones (one planned for 6 Nov, line 250), two duplicate NSB ones, one VYG.
ENTUR = (Path(__file__).parent / 'fixtures' / 'entur-alerts.pb').read_bytes()
ENTUR_NOW = 1793718000      # 2026-11-03 16:20 Oslo time: the RUT real-time alert and both NSB ones are active, the VYG one starts at 17:09
NOW = 1793707200  # 2026-11-03 13:00 UTC, inside the alerts' windows


def pb(number, payload):
    # Minimal protobuf encoder (length-delimited fields only, payloads under 128 bytes) for hand-made feeds.
    return bytes([number << 3 | 2, len(payload)]) + payload


def feed(*headers_and_descriptions):
    entities = b''
    for header, description in headers_and_descriptions:
        text = lambda t: pb(1, pb(1, t.encode()))
        entities += pb(2, pb(1, b'x') + pb(5, pb(10, text(header)) + pb(11, text(description))))
    return entities


class TransitTests(unittest.TestCase):
    def setUp(self):
        transit.CACHE.clear()
        self.real_time = time.time
        time.time = lambda: NOW
        self.addCleanup(setattr, time, 'time', self.real_time)

    def poll(self, config=None, feeds=None, calls=None):
        def handler(request):
            self.assertEqual(request.url.params['key'], 'secret-key')
            region = request.url.path.split('/')[2]
            if calls is not None:
                calls.append(region)
            body = (feeds or {}).get(region, FIXTURE)
            return httpx.Response(500) if body is None else httpx.Response(200, content=body)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['transit'].poll(client, 'https://opendata.samtrafiken.se', config or {'regions': 'krono'}, 'secret-key')
        return asyncio.run(run())

    def test_decodes_real_feed(self):
        alerts = parse_alerts(FIXTURE)
        self.assertEqual(len(alerts), 5)
        self.assertEqual(alerts[0]['header'], 'Indragna hållplatser linje 215,216 och 720 pga vägarbete')
        self.assertEqual(lines(alerts[0]['routes']), [215, 216, 720])
        self.assertEqual(lines(alerts[2]['routes']), [12, 111, 331])
        self.assertTrue(all(len(a['periods']) == 1 and a['periods'][0][1] > a['periods'][0][0] for a in alerts))

    def test_empty_and_open_ended_alerts(self):
        result = self.poll(feeds={'krono': feed((".", "."), ("Linje 9 inställd", "Buss ersätter."))})
        items = result.details['items']
        self.assertEqual([(i['title'], i['state'], i['date']) for i in items], [('Linje 9 inställd', 'Now', '')])   # no period = active, no end date

    def test_truncated_feed_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_alerts(FIXTURE[:-5])

    def test_result_lists_active_alerts(self):
        result = self.poll()
        self.assertEqual([m['value'] for m in result.metrics], ['5', '0'])
        self.assertEqual(result.details['hero'], {'icon': 'triangle-alert', 'title': '5 active', 'text': 'Kronoberg'})
        titles = [i['title'] for i in result.details['items']]
        self.assertEqual(len(titles), 5)
        first = result.details['items'][0]
        self.assertEqual(first['state'], 'Now')
        self.assertTrue(first['date'].startswith('until '))
        self.assertNotIn('secret-key', str(result))

    def test_line_filter_uses_route_ids_and_headline(self):
        result = self.poll({'regions': 'krono', 'filter': '215'})
        self.assertEqual([i['title'] for i in result.details['items']], ['Indragna hållplatser linje 215,216 och 720 pga vägarbete'])
        self.assertTrue(result.details['items'][0]['subtitle'].startswith('Line 215, 216, 720 · '))
        self.assertEqual(len(self.poll({'regions': 'krono', 'filter': '12'}).details['items']), 1)   # not 111/215 by substring
        self.assertEqual(len(self.poll({'regions': 'krono', 'filter': '1'}).details['items']), 0)    # whole numbers only

    def test_word_filter_searches_description(self):
        self.assertEqual(len(self.poll({'regions': 'krono', 'filter': 'växjö station'}).details['items']), 1)
        self.assertEqual(self.poll({'regions': 'krono', 'filter': 'nowhere'}).details['hero']['title'], 'All clear')

    def test_future_alerts_are_planned_and_expired_are_hidden(self):
        time.time = lambda: NOW - 40 * 86400
        result = self.poll()
        self.assertEqual([m['value'] for m in result.metrics], ['0', '5'])
        self.assertTrue(result.details['items'][0]['date'].startswith('from '))
        time.time = lambda: NOW + 365 * 86400
        self.assertEqual(self.poll().details['items'], [])

    def test_regions_are_merged_deduplicated_and_survive_one_failure(self):
        calls = []
        result = self.poll({'regions': 'krono, klt'}, feeds={'klt': FIXTURE}, calls=calls)   # same alerts in both feeds
        self.assertEqual(sorted(calls), ['klt', 'krono'])
        self.assertEqual(len(result.details['items']), 5)
        partial = self.poll({'regions': 'krono, jlt'}, feeds={'jlt': None})
        self.assertEqual(len(partial.details['items']), 5)
        self.assertEqual(partial.details['note'], '1 of 2 regions could not be fetched.')
        transit.CACHE.clear()
        with self.assertRaises(httpx.HTTPStatusError):
            self.poll({'regions': 'krono'}, feeds={'krono': None})

    def test_feeds_are_throttled(self):
        calls = []
        self.poll(calls=calls)
        self.poll(calls=calls)
        self.assertEqual(calls, ['krono'])
        transit.CACHE.clear()
        self.poll(calls=calls)
        self.assertEqual(calls, ['krono', 'krono'])

    def test_validation_and_manifest(self):
        t = REGISTRY['transit']
        self.assertEqual(t.validate({'regions': 'krono,klt, krono', 'filter': ' 12 , Växjö '}), {'country': 'se', 'regions': 'krono, klt', 'filter': '12, Växjö'})
        for bad in ({}, {'regions': 'nowhere'}, {'regions': 'sl,ul,krono,klt,jlt'}, {'regions': 'krono', 'filter': ','.join(map(str, range(9)))}):
            with self.assertRaises(ValueError):
                t.validate(bad)
        entry = next(p for p in manifest() if p['id'] == 'transit')
        self.assertEqual(entry['placement'], 'header')
        self.assertEqual([c['value'] for c in entry['countries']], ['se', 'no'])
        self.assertEqual(entry['default_country'], 'se')
        self.assertIn({'value': 'krono', 'label': 'Kronoberg'}, entry['variants']['se']['fields'][0]['options'])
        self.assertTrue(entry['variants']['se']['secret_label'])
        self.assertEqual(entry['variants']['no']['secret_label'], '')               # Norway needs no key
        self.assertIn('Entur', entry['variants']['no']['notice'])

    def test_matches_helper(self):
        alert = {'header': 'Linje 12, 111', 'description': 'Hållplats Sjöudden', 'routes': ['9011007033100000']}
        self.assertTrue(matches(alert, ['331']))      # via route id
        self.assertTrue(matches(alert, ['sjöudden']))
        self.assertFalse(matches(alert, ['2']))
        self.assertTrue(matches(alert, []))

    def poll_no(self, config, calls=None):
        def handler(request):
            self.assertEqual(request.url.path, '/realtime/v1/gtfs-rt/alerts')
            self.assertNotIn('key', request.url.params)
            self.assertEqual(request.headers['ET-Client-Name'], config.get('client_name', 'homelab_homedash'))
            if calls is not None:
                calls.append(1)
            return httpx.Response(200, content=ENTUR)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['transit'].poll(client, 'https://api.entur.io', {'country': 'no', **config}, '')
        return asyncio.run(run())

    def test_norway_decodes_entur_feed_and_filters_by_operator_code(self):
        time.time = lambda: ENTUR_NOW
        self.assertEqual(len(parse_alerts(ENTUR)), 5)
        result = self.poll_no({'filter': 'RUT'})
        self.assertEqual([i['title'] for i in result.details['items']], ['Real time updates not working', 'Linje 250: Holdeplass Oksenøyveien blir ikke betjent.'])
        self.assertEqual([i['state'] for i in result.details['items']], ['Now', 'Planned'])
        self.assertEqual(result.details['hero'], {'icon': 'triangle-alert', 'title': '1 active', 'text': 'Norway · filter: RUT'})
        planned = result.details['items'][1]
        self.assertEqual(planned['date'], 'from 6 Nov')
        self.assertEqual([m['value'] for m in result.metrics], ['1', '1'])

    def test_norway_line_and_word_filters_and_duplicates(self):
        time.time = lambda: ENTUR_NOW
        self.assertEqual([i['title'] for i in self.poll_no({'filter': '250'}).details['items']][0][:9], 'Linje 250')   # whole number in the headline
        self.assertEqual(len(self.poll_no({'filter': 'Gjøvik'}).details['items']), 1)                              # the two identical NSB alerts merge
        self.assertEqual(self.poll_no({'filter': 'Bergen'}).details['hero']['title'], 'All clear')

    def test_norway_validation_and_client_name(self):
        t = REGISTRY['transit']
        self.assertEqual(t.validate({'country': 'no', 'filter': ' Oslo , R14 '}), {'country': 'no', 'filter': 'Oslo, R14', 'client_name': 'homelab_homedash'})
        self.assertEqual(t.validate({'country': 'no', 'filter': 'Oslo', 'client_name': ' Maja-HomeDash '})['client_name'], 'maja-homedash')
        for bad in ({'country': 'no'}, {'country': 'no', 'filter': ' , '}, {'country': 'no', 'filter': 'Oslo', 'client_name': 'has space'},
                    {'country': 'no', 'filter': 'Oslo', 'client_name': 'x'}, {'country': 'dk', 'filter': 'x'}):
            with self.assertRaises(ValueError):
                t.validate(bad)
        self.assertEqual(t.provider({}).country_name, 'Sweden')                       # saved widgets without a country stay Swedish

    def test_norway_feed_is_throttled_without_a_key(self):
        time.time = lambda: ENTUR_NOW
        calls = []
        self.poll_no({'filter': 'RUT'}, calls)
        self.poll_no({'filter': 'NSB'}, calls)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
