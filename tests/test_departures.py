import asyncio
import json
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from connectors import REGISTRY, departures, manifest

# Real Trafiklab Timetables responses for Växjö station on 2026-11-03: departures queried 14:20, arrivals 15:32.
# Line 9 has no live data in the departures; most arrivals do, because those buses are already running.
FIX = Path(__file__).parent / 'fixtures'
DEPARTURES = json.loads((FIX / 'departures-vaxjo-station.json').read_text())
ARRIVALS = json.loads((FIX / 'arrivals-vaxjo-station.json').read_text())
CONFIG = {'stop': '740000250', 'stop_name': 'Växjö station', 'lines': '9, 14, 123'}


def aligned(arrivals, to):
    """Shift the arrivals fixture in time so both lists share one 'now' (the fixtures were captured an hour apart)."""
    delta = datetime.fromisoformat(to) - datetime.fromisoformat(arrivals['timestamp'])
    shift = lambda t: (datetime.fromisoformat(t) + delta).isoformat()
    out = json.loads(json.dumps(arrivals))
    out['timestamp'] = shift(out['timestamp'])
    for call in out['arrivals']:
        call['scheduled'], call['realtime'] = shift(call['scheduled']), shift(call['realtime'])
    return out


class DeparturesTests(unittest.TestCase):
    def setUp(self):
        departures.CACHE.clear()
        departures.ERRORS.clear()

    def poll(self, config=None, dep=None, arr=None, calls=None, arr_status=200, dep_status=200):
        def handler(request):
            kind = request.url.path.split('/')[2]
            self.assertEqual(request.url.path, f'/v1/{kind}/740000250')
            self.assertEqual(request.url.params['key'], 'secret-key')
            if calls is not None:
                calls.append(kind)
            if kind == 'departures':
                return httpx.Response(dep_status, json=dep or DEPARTURES)
            return httpx.Response(arr_status, json=arr or ARRIVALS)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['departures'].poll(client, 'https://realtime-api.trafiklab.se', config or CONFIG, 'secret-key')
        return asyncio.run(run())

    def test_departures_mode_headline_and_both_lists(self):
        result = self.poll()
        by = {m['label']: m for m in result.metrics}
        self.assertEqual((by['Line 9']['value'], by['Line 9']['unit']), ('11 min', 'scheduled'))   # 14:31 against a 14:20:11 timestamp
        self.assertEqual(by['Line 14'], {'label': 'Line 14', 'value': '—', 'unit': 'none within the hour'})
        self.assertEqual(result.details['hero'], {'icon': 'bus', 'title': '9 in 11 min', 'text': 'Bredvik · Växjö station'})
        self.assertEqual(result.summary, 'scheduled')
        deps, arrs = result.details['lists']
        self.assertEqual((deps['heading'], arrs['heading']), ('Departures', 'Arrivals'))
        self.assertEqual({i['state'] for i in deps['items']}, {'Scheduled'})              # never "on time" without live data
        self.assertIn('→ Bredvik', deps['items'][0]['title'])
        # Line 9 has no live data when it departs, but its arrivals do: those buses are already running.
        self.assertEqual([(i['title'], i['state']) for i in arrs['items']][:2], [('9 ← Hagen', '+1 min'), ('9 ← Stortorget', 'Scheduled')])
        self.assertEqual(self.poll({**CONFIG, 'lines': '14'}).details['lists'][1]['empty'], 'No matching arrivals in the next hour.')
        self.assertIn('not always available or accurate', result.details['note'])
        self.assertNotIn('secret-key', str(result))

    def test_arrivals_mode_shows_origin_and_lists_arrivals_first(self):
        result = self.poll({'stop': '740000250', 'stop_name': 'Växjö station', 'show': 'arrivals', 'lines': '355'})
        arrs, deps = result.details['lists']
        self.assertEqual((arrs['heading'], deps['heading']), ('Arrivals', 'Departures'))
        first = arrs['items'][0]
        self.assertTrue(first['title'].startswith('355 ← Braås centrum'))                    # arrivals say where they come from
        self.assertEqual(first['state'], '+8 min')                                           # live: delay 476 s
        self.assertEqual(first['date'], '15:25 → 15:32')
        self.assertEqual(result.details['hero']['title'], '355 arrives now')
        self.assertEqual(result.details['hero']['text'], 'from Braås centrum · Växjö station')
        self.assertEqual(result.summary, '+8 min')
        self.assertEqual(first['subtitle'], 'now · platform Q')
        self.assertEqual(len(self.poll({'stop': '740000250', 'show': 'arrivals'}).details['lists'][0]['items']), 5)   # at most five per list

    def test_next_mode_picks_whichever_comes_first(self):
        arrivals = aligned(ARRIVALS, DEPARTURES['timestamp'])
        result = self.poll({'stop': '740000250', 'stop_name': 'Växjö station', 'show': 'next', 'lines': ''}, arr=arrivals)
        merged = sorted([(c['realtime'], 'departs', c['route']['designation']) for c in DEPARTURES['departures'] if c['realtime'] >= '2026-11-03T14:19'] +
                        [(c['realtime'], 'arrives', c['route']['designation']) for c in arrivals['arrivals'] if c['realtime'] >= '2026-11-03T14:19'])
        _, verb, line = merged[0]
        self.assertTrue(result.details['hero']['title'].startswith(f'{line} {verb} '), result.details['hero']['title'])
        followed = self.poll({'stop': '740000250', 'stop_name': '', 'show': 'next', 'lines': '9, 3'}, arr=arrivals)
        units = [m['unit'] for m in followed.metrics]
        self.assertTrue(all(u.startswith(('dep · ', 'arr · ')) for u in units), units)       # says which kind each next one is

    def test_live_delays_cancellations_and_early_departures(self):
        data = json.loads(json.dumps(DEPARTURES))
        live = [c for c in data['departures'] if c['is_realtime']][:3]
        late, cancelled, early = live
        late.update(delay=320, realtime=late['scheduled'][:14] + '30:00')
        cancelled.update(canceled=True)
        early.update(delay=-229)
        data['departures'] = live                                                         # exactly these three
        items = self.poll({'stop': '740000250', 'stop_name': '', 'lines': ''}, dep=data).details['lists'][0]['items']
        self.assertEqual(sorted(i['state'] for i in items), ['+5 min', '-4 min', 'Cancelled'])
        self.assertIn(' → 14:30', next(i for i in items if i['state'] == '+5 min')['date'])

    def test_already_left_are_dropped_as_the_copy_ages(self):
        later = json.loads(json.dumps(DEPARTURES))
        later['timestamp'] = '2026-11-03T14:40:00'
        result = self.poll(dep=later)
        self.assertEqual(result.details['lists'][0]['items'][0]['subtitle'].split(' · ')[0], 'in 11 min')   # the 14:51 line 9; 14:31 is gone

    def test_both_lists_fetched_once_and_the_one_not_needed_refreshes_slowly(self):
        calls = []
        self.poll(calls=calls)
        self.assertEqual(sorted(calls), ['arrivals', 'departures'])
        self.poll(calls=calls)
        self.poll({**CONFIG, 'lines': '3'}, calls=calls)                                      # a different filter shares the same stop
        self.assertEqual(len(calls), 2)
        for url, (stamp, data) in list(departures.CACHE.items()):                              # age both copies to 150 s
            departures.CACHE[url] = (stamp - 150, data)
        self.poll(calls=calls)                                                                  # departures mode: departures refresh (>120 s), arrivals do not (<300 s)
        self.assertEqual(calls[2:], ['departures'])
        for url, (stamp, data) in list(departures.CACHE.items()):
            departures.CACHE[url] = (stamp - 150, data)
        self.poll({**CONFIG, 'show': 'next'}, calls=calls)                                      # next mode needs both fresh
        self.assertEqual(sorted(calls[3:]), ['arrivals', 'departures'])

    def test_failures_back_off_and_a_missing_secondary_list_is_tolerated(self):
        calls = []
        partial = self.poll(arr_status=429, calls=calls)                                        # arrivals not needed for the headline
        self.assertEqual(partial.details['lists'][1]['empty'], 'Arrivals could not be fetched.')
        self.assertEqual(partial.details['hero']['title'], '9 in 11 min')
        self.poll(calls=calls, arr_status=200)
        self.assertEqual(calls.count('arrivals'), 1)                                            # inside the back-off window: not retried
        departures.CACHE.clear()
        departures.ERRORS.clear()
        with self.assertRaises(httpx.HTTPStatusError):                                          # the list the headline follows must work
            self.poll({**CONFIG, 'show': 'arrivals'}, arr_status=429)

    def test_nothing_due(self):
        empty = {**DEPARTURES, 'departures': []}
        result = self.poll(dep=empty)
        self.assertEqual(result.details['hero']['title'], 'Nothing due')
        self.assertEqual(result.details['lists'][0]['items'], [])

    def test_validation_and_manifest(self):
        d = REGISTRY['departures']
        self.assertEqual(d.validate({'stop': ' 740000250 ', 'lines': '9, 14 ,9,42X'}),
                         {'country': 'se', 'stop': '740000250', 'stop_name': '', 'show': 'departures', 'lines': '9, 14, 42X', 'modes': ''})   # saved widgets default to Sweden and departures
        self.assertEqual(d.validate({'stop': '740000250', 'show': 'next'})['show'], 'next')
        for bad in ({}, {'stop': 'abc'}, {'stop': '740000250', 'lines': '1,2,3,4,5,6,7'}, {'stop': '740000250', 'lines': 'a/b'}, {'stop': '740000250', 'show': 'both'}):
            with self.assertRaises(ValueError):
                d.validate(bad)
        entry = next(p for p in manifest() if p['id'] == 'departures')
        self.assertEqual((entry['placement'], [c['value'] for c in entry['countries']], entry['default_country']), ('header', ['se', 'no'], 'se'))
        for country, secret in (('se', True), ('no', False)):
            variant = entry['variants'][country]
            self.assertEqual(variant['search']['label'], 'Find a stop')
            self.assertEqual(bool(variant['secret_label']), secret)                       # Norway needs no key
            self.assertEqual([c['value'] for c in next(f for f in variant['fields'] if f['key'] == 'show')['choices']], ['departures', 'arrivals', 'next'])
            self.assertIn('not always available or accurate', variant['notice'])
        self.assertIn('Trafiklab', entry['variants']['se']['notice'])
        self.assertIn('Entur', entry['variants']['no']['notice'])
        self.assertTrue(next(p for p in manifest() if p['id'] == 'transit')['variants']['se']['notice'])

    def test_stop_search(self):
        def handler(request):
            self.assertEqual(request.url.raw_path.split(b'?')[0], b'/v1/stops/name/V%C3%A4xj%C3%B6')
            self.assertEqual(request.url.params['key'], 'secret-key')
            return httpx.Response(200, json={'stop_groups': [{'id': '740000250', 'name': 'Växjö station', 'transport_modes': ['BUS', 'TRAIN'], 'stops': []}]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['departures'].provider({}).search(client, 'Växjö', 'secret-key', {})
        self.assertEqual(asyncio.run(run()), [{'label': 'Växjö station · Bus, Train', 'values': {'stop': '740000250', 'stop_name': 'Växjö station'}}])

    def test_every_row_has_a_vehicle_icon_and_label(self):
        items = self.poll({'stop': '740000250', 'stop_name': '', 'lines': '9, 17519'}, dep=self.with_modes()).details['lists'][0]['items']
        by = {i['title'].split(' ')[0]: i for i in items}
        self.assertEqual((by['9']['icon'], by['9']['icon_label']), ('bus', 'Bus'))
        self.assertEqual((by['17519']['icon'], by['17519']['icon_label']), ('train-front', 'Train'))
        arrivals = self.poll({'stop': '740000250', 'stop_name': '', 'show': 'arrivals', 'lines': '17560'}).details['lists'][0]['items']
        self.assertEqual((arrivals[0]['icon'], arrivals[0]['icon_label']), ('train-front', 'Train'))

    @staticmethod
    def with_modes():
        return json.loads(json.dumps(DEPARTURES))

    def test_vehicle_type_filter(self):
        cfg = lambda modes: {'stop': '740000250', 'stop_name': '', 'modes': modes}
        trains = self.poll(cfg('train'))
        items = trains.details['lists'][0]['items']
        self.assertTrue(items and {i['icon_label'] for i in items} == {'Train'})                 # only trains, no buses
        self.assertEqual(trains.details['hero']['icon'], 'train-front')
        buses = self.poll(cfg('bus')).details['lists'][0]['items']
        self.assertEqual({i['icon_label'] for i in buses}, {'Bus'})
        both = self.poll(cfg('bus, train')).details['lists'][0]['items']
        self.assertEqual({i['icon_label'] for i in both}, {'Bus', 'Train'})
        self.assertEqual(self.poll(cfg('tram')).details['hero']['title'], 'Nothing due')         # nothing of that kind here
        self.assertEqual(self.poll(cfg('train'), arr=None).details['lists'][1]['items'][0]['icon_label'], 'Train')   # arrivals filtered too

    def test_swedish_replacement_bus_is_a_bus(self):
        data = self.with_modes()
        train = next(c for c in data['departures'] if c['route']['transport_mode'] == 'TRAIN')
        train['route']['transport_mode_code'] = 714                                                # GTFS extended type: rail replacement bus
        line = train['route']['designation']
        result = self.poll({'stop': '740000250', 'stop_name': '', 'modes': 'bus', 'lines': line}, dep=data)
        item = result.details['lists'][0]['items'][0]
        self.assertEqual((item['icon'], item['icon_label']), ('bus', 'Replacement bus'))
        self.assertTrue(item['subtitle'].endswith('replacement bus'))
        self.assertEqual((result.details['hero']['icon'], result.details['hero']['text'].split(' · ')[-1]), ('bus', 'replacement bus'))
        self.assertEqual(self.poll({'stop': '740000250', 'stop_name': '', 'modes': 'train', 'lines': line}, dep=data).details['hero']['title'], 'Nothing due')

    def test_mode_validation(self):
        d = REGISTRY['departures']
        self.assertEqual(d.validate({'stop': '740000250', 'modes': ' train , bus,train'})['modes'], 'bus, train')    # canonical order, no repeats
        with self.assertRaises(ValueError):
            d.validate({'stop': '740000250', 'modes': 'bus, plane'})
        entry = next(p for p in manifest() if p['id'] == 'departures')
        for country in ('se', 'no'):
            field = next(f for f in entry['variants'][country]['fields'] if f['key'] == 'modes')
            self.assertEqual([o['value'] for o in field['options']], ['bus', 'train', 'tram', 'metro', 'boat'])
            self.assertTrue(field['optional'])


ENTUR_DEPARTURES = json.loads((FIX / 'entur-departures-oslo-s.json').read_text())
ENTUR_ARRIVALS = json.loads((FIX / 'entur-arrivals-oslo-s.json').read_text())
NO = {'country': 'no', 'stop': 'NSR:StopPlace:59872', 'stop_name': 'Oslo S', 'show': 'next', 'lines': ''}


class NorwayTests(unittest.TestCase):
    """Real Entur Journey Planner responses for Oslo S, captured 2026-11-03 around 16:10 Oslo time (+01:00)."""
    def setUp(self):
        departures.CACHE.clear()
        departures.ERRORS.clear()
        self.addCleanup(setattr, departures.Norway, 'utcnow', departures.Norway.utcnow)
        departures.Norway.utcnow = staticmethod(lambda: datetime(2026, 11, 3, 15, 10, 0, tzinfo=__import__('datetime').timezone.utc))   # 16:10 in Oslo

    def poll(self, config=None, dep=None, arr=None, calls=None):
        def handler(request):
            self.assertEqual((request.method, request.url.path), ('POST', '/journey-planner/v3/graphql'))
            self.assertEqual(request.headers['ET-Client-Name'], 'homelab_homedash')
            body = json.loads(request.content)
            self.assertEqual(body['variables'], {'id': 'NSR:StopPlace:59872'})
            kind = 'arrivals' if 'arrivalDeparture: arrivals' in body['query'] else 'departures'
            if calls is not None:
                calls.append(kind)
            return httpx.Response(200, json=(arr or ENTUR_ARRIVALS) if kind == 'arrivals' else (dep or ENTUR_DEPARTURES))
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['departures'].poll(client, 'https://api.entur.io', config or NO, '')
        return asyncio.run(run())

    def test_both_lists_normalised_and_headline_is_whichever_comes_first(self):
        result = self.poll()
        deps, arrs = result.details['lists']
        self.assertEqual((deps['heading'], arrs['heading']), ('Departures', 'Arrivals'))
        self.assertEqual(deps['items'][0]['title'], 'L1 → ' + ENTUR_DEPARTURES['data']['stopPlace']['estimatedCalls'][0]['destinationDisplay']['frontText'])
        self.assertEqual((deps['items'][0]['date'], deps['items'][0]['state']), ('16:11 → 16:12', '+1 min'))   # offset stripped: local time, live delay 60 s
        first = arrs['items'][0]                                                               # sorted by when it really arrives
        self.assertRegex(first['title'], r'^L2 ← \S')                                          # arrivals say where they come from
        self.assertEqual((first['date'], first['state']), ('16:14 → 16:11', '-3 min'))        # live and three minutes early
        self.assertEqual(result.details['hero']['title'], 'L2 arrives in 1 min')              # 16:11 arrival beats the 16:12 departure
        self.assertRegex(result.details['hero']['text'], r'^from \S.* · Oslo S$')
        self.assertIn('Entur', result.details['note'])
        self.assertEqual(result.details['hero']['icon'], 'train-front')                        # rail

    def test_line_filter_and_arrival_only_mode(self):
        result = self.poll({**NO, 'show': 'arrivals', 'lines': 'R13, L2'})
        self.assertEqual(result.details['lists'][0]['heading'], 'Arrivals')
        self.assertEqual({i['title'].split(' ')[0] for i in result.details['lists'][0]['items']}, {'R13', 'L2'})
        self.assertEqual(result.details['hero']['title'], 'L2 arrives in 1 min')
        self.assertEqual([m['label'] for m in result.metrics], ['Line R13', 'Line L2'])

    def test_not_live_cancelled_and_late(self):
        data = json.loads(json.dumps(ENTUR_ARRIVALS))
        calls = data['data']['stopPlace']['estimatedCalls']
        by_line = lambda code: next(c for c in calls if c['serviceJourney']['line']['publicCode'] == code)
        self.assertFalse(by_line('70')['realtime'])                                            # a bus the operator does not track live
        late = by_line('RE11')
        late['expectedArrivalTime'] = (datetime.fromisoformat(late['aimedArrivalTime']) + timedelta(minutes=5)).isoformat()
        by_line('FLY2')['cancellation'] = True
        items = self.poll({**NO, 'show': 'arrivals', 'lines': 'RE11, FLY2, 70'}, arr=data).details['lists'][0]['items']
        by = {i['title'].split(' ')[0]: i for i in items}
        self.assertEqual(by['70']['state'], 'Scheduled')                                       # never "on time" without live data
        self.assertEqual((by['RE11']['state'], by['RE11']['date']), ('+5 min', '16:21 → 16:26'))
        self.assertEqual(by['FLY2']['state'], 'Cancelled')

    def test_unknown_stop_and_throttling(self):
        with self.assertRaises(ValueError):
            self.poll(dep={'data': {'stopPlace': None}}, config={**NO, 'show': 'departures'})
        departures.CACHE.clear()
        departures.ERRORS.clear()
        calls = []
        self.poll(calls=calls)
        self.poll(calls=calls)
        self.assertEqual(sorted(calls), ['arrivals', 'departures'])                           # one fetch of each, then served from the cache
        self.assertEqual((departures.Norway.throttle, departures.Norway.slow), (60, 180))      # Entur allows far more than Trafiklab

    def test_vehicle_icons_and_replacement_buses(self):
        items = self.poll({**NO, 'lines': 'L1, 70'}).details['lists'][0]['items']
        self.assertEqual({(i['icon'], i['icon_label']) for i in items}, {('train-front', 'Train')})     # Oslo S is all rail in this fixture
        data = json.loads(json.dumps(ENTUR_DEPARTURES))
        l1 = data['data']['stopPlace']['estimatedCalls'][0]
        self.assertEqual(l1['serviceJourney']['line']['transportMode'], 'rail')
        l1['serviceJourney']['transportSubmode'] = 'railReplacementBus'                                  # Entur's line still says "rail"
        departures.CACHE.clear()                                                                           # the earlier poll cached the unmodified data
        result = self.poll({**NO, 'show': 'departures', 'lines': 'L1', 'modes': 'bus'}, dep=data)
        item = result.details['lists'][0]['items'][0]
        self.assertEqual((item['icon'], item['icon_label']), ('bus', 'Replacement bus'))
        self.assertTrue(item['subtitle'].endswith('replacement bus'))
        self.assertEqual(result.details['hero']['icon'], 'bus')
        departures.CACHE.clear()
        only_trains = self.poll({**NO, 'show': 'departures', 'lines': 'L1', 'modes': 'train'}, dep=data).details['lists'][0]['items']
        self.assertTrue(all(i['icon_label'] == 'Train' for i in only_trains))                           # the replacement bus is not a train here
        self.assertEqual(len(only_trains), len(self.poll({**NO, 'show': 'departures', 'lines': 'L1'}).details['lists'][0]['items']) - 1)

    def test_validation(self):
        d = REGISTRY['departures']
        self.assertEqual(d.validate({'country': 'no', 'stop': ' NSR:StopPlace:59872 ', 'lines': 'R14, L1'}),
                         {'country': 'no', 'stop': 'NSR:StopPlace:59872', 'client_name': 'homelab_homedash', 'stop_name': '', 'show': 'departures', 'lines': 'R14, L1', 'modes': ''})
        for bad in ({'country': 'no', 'stop': '740000250'}, {'country': 'no', 'stop': 'NSR:Quay:1'}, {'country': 'se', 'stop': 'NSR:StopPlace:59872'},
                    {'country': 'no', 'stop': 'NSR:StopPlace:59872', 'client_name': 'Has Space'}, {'country': 'dk', 'stop': '1'}):
            with self.assertRaises(ValueError):
                d.validate(bad)

    def test_stop_search_uses_geocoder_and_client_name(self):
        def handler(request):
            self.assertEqual(request.url.host, 'api.entur.io')
            self.assertEqual(request.url.path, '/geocoder/v3/autocomplete')
            self.assertEqual((request.url.params['q'], request.url.params['layers']), ('oslo s', 'stopPlace'))
            self.assertEqual(request.headers['ET-Client-Name'], 'my_name-homedash')
            return httpx.Response(200, json={'features': [{'properties': {'id': 'NSR:StopPlace:59872', 'names': {'default': 'Oslo S', 'display': 'Oslo S, Oslo'}}}]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['departures'].provider({'country': 'no'}).search(client, 'oslo s', '', {'client_name': 'My_Name-HomeDash'})
        self.assertEqual(asyncio.run(run()), [{'label': 'Oslo S, Oslo', 'values': {'stop': 'NSR:StopPlace:59872', 'stop_name': 'Oslo S'}}])


if __name__ == '__main__':
    unittest.main()
