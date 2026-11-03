"""Current conditions and a five-day forecast from Open-Meteo (no API key)."""
import asyncio
from bisect import bisect_right
from urllib.parse import urlencode

# WMO weather interpretation code -> (description, Lucide icon).
CODES = {0:('Clear','sun'),1:('Mostly clear','sun'),2:('Partly cloudy','cloud-sun'),3:('Overcast','cloud'),
         45:('Fog','cloud-fog'),48:('Fog','cloud-fog'),
         51:('Light drizzle','cloud-drizzle'),53:('Drizzle','cloud-drizzle'),55:('Heavy drizzle','cloud-drizzle'),
         56:('Freezing drizzle','cloud-drizzle'),57:('Freezing drizzle','cloud-drizzle'),
         61:('Light rain','cloud-rain'),63:('Rain','cloud-rain'),65:('Heavy rain','cloud-rain'),
         66:('Freezing rain','cloud-rain'),67:('Freezing rain','cloud-rain'),
         71:('Light snow','cloud-snow'),73:('Snow','cloud-snow'),75:('Heavy snow','cloud-snow'),77:('Snow grains','snowflake'),
         80:('Light showers','cloud-sun-rain'),81:('Showers','cloud-sun-rain'),82:('Heavy showers','cloud-rain'),
         85:('Snow showers','cloud-snow'),86:('Snow showers','cloud-snow'),
         95:('Thunderstorm','cloud-lightning'),96:('Thunderstorm with hail','cloud-lightning'),99:('Thunderstorm with hail','cloud-lightning')}
NIGHT = {'sun':'moon','cloud-sun':'cloud-moon'}
GEOCODING = 'https://geocoding-api.open-meteo.com'
AIR = 'https://air-quality-api.open-meteo.com'
# European Air Quality Index bands: 0-20 good ... above 100 extremely poor.
EAQI = ['Good', 'Fair', 'Moderate', 'Poor', 'Very poor', 'Extremely poor']
POLLEN = {'alder_pollen': 'Alder', 'birch_pollen': 'Birch', 'grass_pollen': 'Grass', 'mugwort_pollen': 'Mugwort', 'olive_pollen': 'Olive', 'ragweed_pollen': 'Ragweed'}


def num(value, spec='.0f'):
    return '—' if value is None else format(value, spec)


def rain_text(chance, amount):
    parts = [f'{chance:.0f}%'] if chance is not None else []
    if amount:
        parts.append(f'{amount:.1f} mm')
    return ' · '.join(parts) or 'None'


def air_quality(air):
    """Air-quality metrics plus a pollen tile. Pollen is Europe-only and null out of season, so it appears only when present."""
    now = (air or {}).get('current') or {}
    metrics, tiles = [], []
    if now.get('european_aqi') is not None:
        metrics.append({'label': 'Air quality', 'value': EAQI[bisect_right([20, 40, 60, 80, 100], now['european_aqi'])], 'unit': f"AQI {now['european_aqi']:.0f}"})
    if now.get('pm2_5') is not None:
        metrics.append({'label': 'PM2.5', 'value': num(now['pm2_5'], '.1f'), 'unit': 'µg/m³'})
    # ponytail: raw grains/m³ without Low/High labels, because the thresholds differ per species. Add a per-species table if wanted.
    pollen = sorted(((now[key], name) for key, name in POLLEN.items() if (now.get(key) or 0) >= 1), reverse=True)
    if pollen:
        tiles.append({'heading': 'Pollen', 'icon': 'flower-2', 'metrics': [{'label': name, 'value': f'{count:.0f} grains/m³'} for count, name in pollen]})
    return metrics, tiles


def condition(code, night=False):
    text, icon = CODES.get(code, ('Unknown', 'cloud'))
    return text, NIGHT.get(icon, icon) if night else icon


async def search_cities(client, query):
    from . import fetch
    data = await fetch(client, f'{GEOCODING}/v1/search?' + urlencode({'name': query, 'count': 6, 'language': 'en'}))
    found = []
    for r in data.get('results', []):
        place = ', '.join(dict.fromkeys(filter(None, (r['name'], r.get('admin1'), r.get('country')))))
        found.append({'label': place, 'values': {'latitude': f"{r['latitude']:g}", 'longitude': f"{r['longitude']:g}", 'place': place}})
    return found


class Weather:
    id = 'weather'
    name = 'Weather'
    description = 'Current conditions and a five-day forecast, air quality and (in Europe) pollen from Open-Meteo. Shown in the header. Your coordinates are sent to the API URL below; use a self-hosted instance if you prefer.'
    secret_label = ''
    placement = 'header'
    default_url = 'https://api.open-meteo.com'
    search_ui = {'label': 'Find a city', 'placeholder': 'Stockholm'}

    async def search(self, client, query, secret, config):
        return await search_cities(client, query)

    fields = [{'key':'latitude','label':'Latitude','placeholder':'59.33','help':'Decimal degrees, −90 to 90. Search for a city above to fill this in.'},
              {'key':'longitude','label':'Longitude','placeholder':'18.07','help':'Decimal degrees, −180 to 180.'},
              {'key':'place','label':'Place name','placeholder':'Stockholm, Sweden','help':'Shown in the details panel.','optional':True}]

    def validate(self, config):
        try:
            lat, lon = float(config.get('latitude', '')), float(config.get('longitude', ''))
        except ValueError:
            raise ValueError('Enter latitude and longitude as decimal numbers') from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('Latitude must be within ±90 and longitude within ±180')
        return {'latitude': f'{lat:g}', 'longitude': f'{lon:g}', 'place': str(config.get('place', '')).strip()[:100]}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        config = self.validate(config)
        place = f"latitude={config['latitude']}&longitude={config['longitude']}"
        air_base = AIR if base.rstrip('/') == self.default_url else base
        # Air quality is optional: a missing or failing endpoint (e.g. a self-hosted instance without it) must not break the weather.
        data, air = await asyncio.gather(fetch(client, f"{base}/v1/forecast?{place}"
            '&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,wind_gusts_10m,relative_humidity_2m,pressure_msl,is_day'
            '&hourly=temperature_2m,weather_code,precipitation_probability,is_day'
            '&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,precipitation_sum,precipitation_probability_max'
            '&forecast_days=5&timezone=auto'),
            fetch(client, f"{air_base}/v1/air-quality?{place}&current=european_aqi,pm2_5,{','.join(POLLEN)}&timezone=auto"),
            return_exceptions=True)
        if isinstance(data, BaseException):
            raise data
        now, daily, hourly = data['current'], data['daily'], data['hourly']
        text, icon = condition(now['weather_code'], not now.get('is_day', 1))
        # Hourly times are local ("2026-11-03T14:00"), like current.time; start at the current hour.
        first = next((i for i, t in enumerate(hourly['time']) if t >= now['time'][:13] + ':00'), 0)
        strip = []
        for i in range(first, min(first + 12, len(hourly['time']))):
            desc, hour_icon = condition(hourly['weather_code'][i], not hourly['is_day'][i])
            rain = hourly['precipitation_probability'][i]
            strip.append({'label': 'Now' if i == first else hourly['time'][i][11:16], 'icon': hour_icon,
                          'value': f"{num(hourly['temperature_2m'][i])}°", 'sub': f'{rain:.0f}%' if rain else ''})
        sections = []
        for i, day in enumerate(daily['time']):
            desc, day_icon = condition(daily['weather_code'][i])
            sections.append({'heading': ('Today', 'Tomorrow')[i] if i < 2 else day, 'icon': day_icon, 'metrics': [
                {'label': 'Conditions', 'value': desc},
                {'label': 'High / low', 'value': f"{num(daily['temperature_2m_max'][i])}° / {num(daily['temperature_2m_min'][i])}°"},
                {'label': 'Rain', 'value': rain_text(daily['precipitation_probability_max'][i], daily['precipitation_sum'][i])}]})
        air_metrics, air_tiles = air_quality(None if isinstance(air, BaseException) else air)
        compass = 'N NE E SE S SW W NW'.split()
        wind_from = compass[round(now['wind_direction_10m'] / 45) % 8] if now.get('wind_direction_10m') is not None else ''
        metrics = [
            {'label': 'Feels like', 'value': num(now['apparent_temperature']), 'unit': '°C'},
            {'label': f'Wind {wind_from}'.strip(), 'value': num(now['wind_speed_10m']), 'unit': 'km/h'},
            {'label': 'Gusts', 'value': num(now['wind_gusts_10m']), 'unit': 'km/h'},
            {'label': 'Humidity', 'value': num(now['relative_humidity_2m']), 'unit': '%'},
            {'label': 'Pressure', 'value': num(now['pressure_msl']), 'unit': 'hPa'},
            {'label': 'UV index today', 'value': num(daily['uv_index_max'][0], '.0f')},
            {'label': 'Sunrise', 'value': daily['sunrise'][0][11:16]},
            {'label': 'Sunset', 'value': daily['sunset'][0][11:16]}] + air_metrics
        return Result('healthy', text, metrics, {
            'hero': {'icon': icon, 'title': f"{num(now['temperature_2m'])}°C", 'text': text, 'place': config['place']},
            'strip': {'heading': 'Next 12 hours', 'items': strip}, 'sections': sections + air_tiles})
