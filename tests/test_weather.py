import asyncio
import unittest
import httpx
from connectors import REGISTRY, manifest


class WeatherTests(unittest.TestCase):
    def poll(self, config=None):
        def handler(request):
            self.assertEqual(request.url.params['latitude'], '59.33')
            self.assertEqual(request.url.params['longitude'], '18.07')
            return httpx.Response(200, json={
                'current': {'temperature_2m': 11.6, 'apparent_temperature': 9.2, 'weather_code': 61, 'wind_speed_10m': 14.4},
                'daily': {'time': ['2026-11-03', '2026-11-04', '2026-11-05'], 'weather_code': [61, 3, 0],
                          'temperature_2m_max': [12.4, 9.0, 7.5], 'temperature_2m_min': [6.1, 4.0, 1.2]}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['weather'].poll(client, 'https://api.open-meteo.com', config or {'latitude': '59.33', 'longitude': '18.07'}, '')
        return asyncio.run(run())

    def test_current_and_forecast(self):
        result = self.poll()
        self.assertEqual(result.summary, 'Light rain')
        self.assertEqual([m['value'] for m in result.metrics], ['12', '9', '14'])
        self.assertEqual([s['heading'] for s in result.details['sections']], ['Today', 'Tomorrow', '2026-11-05'])
        self.assertEqual(result.details['sections'][0]['metrics'][1]['value'], '12° / 6°')

    def test_validation(self):
        w = REGISTRY['weather']
        self.assertEqual(w.validate({'latitude': '59.330', 'longitude': '18.07'}), {'latitude': '59.33', 'longitude': '18.07'})
        for bad in ({'latitude': '91', 'longitude': '0'}, {'latitude': 'x', 'longitude': '0'}, {}):
            with self.assertRaises(ValueError):
                w.validate(bad)

    def test_header_placement_in_manifest(self):
        self.assertEqual({p['id']: p['placement'] for p in manifest()}['weather'], 'header')
        self.assertEqual({p['id']: p['placement'] for p in manifest()}['http'], 'card')


if __name__ == '__main__':
    unittest.main()
