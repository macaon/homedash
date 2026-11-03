import asyncio
import unittest
import httpx
from connectors import REGISTRY, manifest


AIR_NOW = {'european_aqi': 34, 'pm2_5': 7.46, 'alder_pollen': 0.0, 'birch_pollen': 48.0, 'grass_pollen': 3.0, 'mugwort_pollen': None, 'olive_pollen': None, 'ragweed_pollen': 0.4}


class WeatherTests(unittest.TestCase):
    def poll(self, config=None, air=None, base='https://api.open-meteo.com'):
        self.hosts = []
        def handler(request):
            self.hosts.append(request.url.host)
            if request.url.path == '/v1/air-quality':
                self.assertIn('european_aqi', request.url.params['current'])
                return httpx.Response(500) if air == 'fail' else httpx.Response(200, json={'current': air if air is not None else AIR_NOW})
            self.assertEqual(request.url.params['latitude'], '59.33')
            self.assertEqual(request.url.params['longitude'], '18.07')
            hours = [f'2026-11-0{3 + d}T{h:02d}:00' for d in range(2) for h in range(24)]
            return httpx.Response(200, json={
                'current': {'time': '2026-11-03T14:20', 'temperature_2m': 11.6, 'apparent_temperature': 9.2, 'weather_code': 61, 'wind_speed_10m': 14.4,
                            'wind_direction_10m': 250, 'wind_gusts_10m': 31.2, 'relative_humidity_2m': 83, 'pressure_msl': 1004.4, 'is_day': 1},
                'hourly': {'time': hours, 'temperature_2m': [10.0] * 48, 'weather_code': [3] * 48, 'is_day': [1 if 7 <= h % 24 < 16 else 0 for h in range(48)],
                           'precipitation_probability': [0, 20] * 24},
                'daily': {'time': ['2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-07'], 'weather_code': [61, 3, 0, 0, 0],
                          'temperature_2m_max': [12.4, 9.0, 7.5, 7, 7], 'temperature_2m_min': [6.1, 4.0, 1.2, 1, 1],
                          'sunrise': ['2026-11-03T07:12'] * 5, 'sunset': ['2026-11-03T15:51'] * 5, 'uv_index_max': [1.2] * 5,
                          'precipitation_sum': [2.14, 0, 0, 0, 0], 'precipitation_probability_max': [70, None, 0, 0, 0]}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['weather'].poll(client, base, config or {'latitude': '59.33', 'longitude': '18.07'}, '')
        return asyncio.run(run())

    def test_current_and_forecast(self):
        result = self.poll()
        self.assertEqual(result.summary, 'Light rain')
        values = {m['label']: m['value'] for m in result.metrics}
        self.assertEqual(values, {'Feels like': '9', 'Wind W': '14', 'Gusts': '31', 'Humidity': '83', 'Pressure': '1004',
                                  'UV index today': '1', 'Sunrise': '07:12', 'Sunset': '15:51',
                                  'Air quality': 'Fair', 'PM2.5': '7.5'})
        self.assertEqual(next(m for m in result.metrics if m['label'] == 'Air quality')['unit'], 'AQI 34')
        self.assertEqual(result.details['hero'], {'icon': 'cloud-rain', 'title': '12°C', 'text': 'Light rain', 'place': ''})
        days = result.details['sections'][:5]
        self.assertEqual([d['heading'] for d in days], ['Today', 'Tomorrow', '2026-11-05', '2026-11-06', '2026-11-07'])
        self.assertEqual([d['icon'] for d in days][:3], ['cloud-rain', 'cloud', 'sun'])
        self.assertEqual(days[0]['metrics'][1:], [{'label': 'High / low', 'value': '12° / 6°'}, {'label': 'Rain', 'value': '70% · 2.1 mm'}])
        self.assertEqual(days[1]['metrics'][2]['value'], 'None')

    def test_pollen_tile_lists_species_in_season_only(self):
        pollen = self.poll().details['sections'][-1]
        self.assertEqual((pollen['heading'], pollen['icon']), ('Pollen', 'flower-2'))
        self.assertEqual(pollen['metrics'], [{'label': 'Birch', 'value': '48 grains/m³'}, {'label': 'Grass', 'value': '3 grains/m³'}])
        out_of_season = {'european_aqi': 12, 'pm2_5': 3.0, **{k: None for k in AIR_NOW if k.endswith('_pollen')}}
        result = self.poll(air=out_of_season)
        self.assertEqual(len(result.details['sections']), 5)
        self.assertEqual(next(m for m in result.metrics if m['label'] == 'Air quality')['value'], 'Good')

    def test_air_quality_is_optional(self):
        result = self.poll(air='fail')
        self.assertEqual(result.summary, 'Light rain')
        self.assertNotIn('Air quality', [m['label'] for m in result.metrics])
        self.assertEqual(len(result.details['sections']), 5)

    def test_air_quality_host(self):
        self.poll()
        self.assertEqual(sorted(self.hosts), ['air-quality-api.open-meteo.com', 'api.open-meteo.com'])
        self.poll(base='https://weather.example')           # self-hosted: both endpoints on the configured host
        self.assertEqual(set(self.hosts), {'weather.example'})

    def test_aqi_bands(self):
        from connectors.weather import air_quality
        label = lambda v: air_quality({'current': {'european_aqi': v}})[0][0]['value']
        self.assertEqual([label(v) for v in (0, 20, 40, 60, 80, 100, 101)], ['Good', 'Fair', 'Moderate', 'Poor', 'Very poor', 'Extremely poor', 'Extremely poor'])

    def test_next_hours_strip_starts_at_current_hour(self):
        strip = self.poll().details['strip']
        self.assertEqual(len(strip['items']), 12)
        self.assertEqual([i['label'] for i in strip['items']][:3], ['Now', '15:00', '16:00'])
        self.assertEqual(strip['items'][0]['icon'], 'cloud')          # 14:00 is daytime
        self.assertEqual(strip['items'][2]['icon'], 'cloud')          # clouds have no night variant
        self.assertEqual(strip['items'][1]['sub'], '20%')
        self.assertEqual(strip['items'][-1]['label'], '01:00')        # crosses midnight

    def test_validation(self):
        w = REGISTRY['weather']
        self.assertEqual(w.validate({'latitude': '59.330', 'longitude': '18.07', 'place': ' Stockholm '}), {'latitude': '59.33', 'longitude': '18.07', 'place': 'Stockholm'})
        for bad in ({'latitude': '91', 'longitude': '0'}, {'latitude': 'x', 'longitude': '0'}, {}):
            with self.assertRaises(ValueError):
                w.validate(bad)

    def test_header_placement_in_manifest(self):
        self.assertEqual({p['id']: p['placement'] for p in manifest()}['weather'], 'header')
        self.assertEqual({p['id']: p['placement'] for p in manifest()}['http'], 'card')

    def test_night_icons(self):
        from connectors.weather import condition
        self.assertEqual(condition(0, True), ('Clear', 'moon'))
        self.assertEqual(condition(61, True)[1], 'cloud-rain')

    def test_city_search(self):
        from connectors.weather import search_cities
        def handler(request):
            self.assertEqual(request.url.host, 'geocoding-api.open-meteo.com')
            self.assertEqual(request.url.params['name'], 'Stock holm')
            return httpx.Response(200, json={'results': [{'name': 'Stockholm', 'admin1': 'Stockholm', 'country': 'Sweden', 'latitude': 59.32938, 'longitude': 18.06871}]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await search_cities(client, 'Stock holm')
        self.assertEqual(asyncio.run(run()), [{'label': 'Stockholm, Sweden', 'values': {'latitude': '59.3294', 'longitude': '18.0687', 'place': 'Stockholm, Sweden'}}])


if __name__ == '__main__':
    unittest.main()
