import asyncio
import json
import unittest
from pathlib import Path
import httpx
from connectors import REGISTRY, departures, manifest

# A real Trafiklab Timetables response for Växjö station, queried 2026-11-03 14:20 (line 9 has no live data, lines 1, 3 and 4 do).
FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'departures-vaxjo-station.json').read_text())
CONFIG = {'stop': '740000250', 'stop_name': 'Växjö station', 'lines': '9, 14, 123'}


class DeparturesTests(unittest.TestCase):
    def setUp(self):
        departures.CACHE.clear()
        departures.ERRORS.clear()

    def poll(self, config=None, data=None, status=200, calls=None):
        def handler(request):
            self.assertEqual(request.url.path, '/v1/departures/740000250')
            self.assertEqual(request.url.params['key'], 'secret-key')
            if calls is not None:
                calls.append(1)
            return httpx.Response(status, json=data or FIXTURE)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['departures'].poll(client, 'https://realtime-api.trafiklab.se', config or CONFIG, 'secret-key')
        return asyncio.run(run())

    def test_followed_lines_show_next_departure_and_never_claim_on_time_without_live_data(self):
        result = self.poll()
        by = {m['label']: m for m in result.metrics}
        self.assertEqual(by['Line 9']['value'], '11 min')            # 14:31 against a 14:20:11 timestamp
        self.assertEqual(by['Line 9']['unit'], 'scheduled')
        self.assertEqual(by['Line 14'], {'label': 'Line 14', 'value': '—', 'unit': 'none within the hour'})
        self.assertEqual(result.details['hero']['title'], '9 in 11 min')
        self.assertEqual(result.summary, 'scheduled')
        self.assertEqual({i['state'] for i in result.details['items']}, {'Scheduled'})
        self.assertIn('Bredvik', result.details['items'][0]['title'])
        self.assertIn('not always available or accurate', result.details['note'])
        self.assertNotIn('secret-key', str(result))

    def test_all_lines_with_live_delays_cancellations_and_early_departures(self):
        data = json.loads(json.dumps(FIXTURE))
        calls = data['departures']
        three = next(c for c in calls if c['route']['designation'] == '4' and c['is_realtime'])
        three.update(delay=320, realtime=three['scheduled'][:14] + '30:00')
        one = next(c for c in calls if c['route']['designation'] == '1' and c['is_realtime'])
        one.update(canceled=True)
        early = next(c for c in calls if c['route']['designation'] == '3' and c['is_realtime'])
        early.update(delay=-229)
        result = self.poll({'stop': '740000250', 'stop_name': '', 'lines': ''}, data)
        states = [i['state'] for i in result.details['items']]
        self.assertIn('+5 min', states)
        self.assertIn('Cancelled', states)
        self.assertIn('-4 min', states)
        late = next(i for i in result.details['items'] if i['state'] == '+5 min')
        self.assertIn('→ 14:30', late['date'])
        self.assertEqual(len(result.details['items']), 12)
        self.assertEqual(result.metrics, [])
        self.assertEqual(result.details['hero']['text'].split(' · ')[-1], 'Växjö station')

    def test_departures_already_left_are_dropped_as_the_copy_ages(self):
        late_view = json.loads(json.dumps(FIXTURE))
        late_view['timestamp'] = '2026-11-03T14:40:00'
        result = self.poll(data=late_view)
        self.assertEqual(result.details['items'][0]['subtitle'].split(' · ')[0], 'in 11 min')   # the 14:51 line 9; 14:31 is gone

    def test_stop_is_fetched_once_per_window_and_failures_back_off(self):
        calls = []
        self.poll(calls=calls)
        self.poll(calls=calls)
        self.poll({**CONFIG, 'lines': '3'}, calls=calls)          # a different filter shares the same stop
        self.assertEqual(len(calls), 1)
        departures.CACHE.clear()
        with self.assertRaises(httpx.HTTPStatusError):
            self.poll(status=429, calls=calls)
        with self.assertRaises(httpx.HTTPStatusError):            # inside the back-off window: no further call
            self.poll(status=200, calls=calls)
        self.assertEqual(len(calls), 2)

    def test_no_departures(self):
        empty = {**FIXTURE, 'departures': []}
        result = self.poll(data=empty)
        self.assertEqual(result.details['hero']['title'], 'No departures')
        self.assertEqual(result.details['items'], [])

    def test_validation_and_manifest(self):
        d = REGISTRY['departures']
        self.assertEqual(d.validate({'stop': ' 740000250 ', 'lines': '9, 14 ,9,42X'}), {'stop': '740000250', 'stop_name': '', 'lines': '9, 14, 42X'})
        for bad in ({}, {'stop': 'abc'}, {'stop': '740000250', 'lines': '1,2,3,4,5,6,7'}, {'stop': '740000250', 'lines': 'a/b'}):
            with self.assertRaises(ValueError):
                d.validate(bad)
        entry = next(p for p in manifest() if p['id'] == 'departures')
        self.assertEqual((entry['placement'], entry['search']['label']), ('header', 'Find a stop'))
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
