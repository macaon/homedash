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
                         {'stop': '740000250', 'stop_name': '', 'show': 'departures', 'lines': '9, 14, 42X'})   # saved widgets default to departures
        self.assertEqual(d.validate({'stop': '740000250', 'show': 'next'})['show'], 'next')
        for bad in ({}, {'stop': 'abc'}, {'stop': '740000250', 'lines': '1,2,3,4,5,6,7'}, {'stop': '740000250', 'lines': 'a/b'}, {'stop': '740000250', 'show': 'both'}):
            with self.assertRaises(ValueError):
                d.validate(bad)
        entry = next(p for p in manifest() if p['id'] == 'departures')
        self.assertEqual((entry['placement'], entry['search']['label']), ('header', 'Find a stop'))
        self.assertEqual([c['value'] for c in next(f for f in entry['fields'] if f['key'] == 'show')['choices']], ['departures', 'arrivals', 'next'])
        self.assertIn('not always available or accurate', entry['notice'])
        self.assertTrue(next(p for p in manifest() if p['id'] == 'transit')['notice'])

    def test_stop_search(self):
        def handler(request):
            self.assertEqual(request.url.raw_path.split(b'?')[0], b'/v1/stops/name/V%C3%A4xj%C3%B6')
            self.assertEqual(request.url.params['key'], 'secret-key')
            return httpx.Response(200, json={'stop_groups': [{'id': '740000250', 'name': 'Växjö station', 'transport_modes': ['BUS', 'TRAIN'], 'stops': []}]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['departures'].search(client, 'Växjö', 'secret-key')
        self.assertEqual(asyncio.run(run()), [{'label': 'Växjö station · Bus, Train', 'values': {'stop': '740000250', 'stop_name': 'Växjö station'}}])


if __name__ == '__main__':
    unittest.main()
