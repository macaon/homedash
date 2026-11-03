"""Current conditions and a three-day forecast from Open-Meteo (no API key)."""
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


def condition(code, night=False):
    text, icon = CODES.get(code, ('Unknown', 'cloud'))
    return text, NIGHT.get(icon, icon) if night else icon


async def search_cities(client, query):
    from . import fetch
    data = await fetch(client, f'{GEOCODING}/v1/search?' + urlencode({'name': query, 'count': 6, 'language': 'en'}))
    return [{'place': ', '.join(dict.fromkeys(filter(None, (r['name'], r.get('admin1'), r.get('country'))))),
             'latitude': f"{r['latitude']:g}", 'longitude': f"{r['longitude']:g}"} for r in data.get('results', [])]


class Weather:
    id = 'weather'
    name = 'Weather'
    description = 'Current conditions and a three-day forecast from Open-Meteo. Shown in the header. Your coordinates are sent to the API URL below; use a self-hosted instance if you prefer.'
    secret_label = ''
    placement = 'header'
    default_url = 'https://api.open-meteo.com'
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
        data = await fetch(client, f"{base}/v1/forecast?latitude={config['latitude']}&longitude={config['longitude']}"
            '&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day'
            '&daily=weather_code,temperature_2m_max,temperature_2m_min&forecast_days=3&timezone=auto')
        now, daily = data['current'], data['daily']
        text, icon = condition(now['weather_code'], not now.get('is_day', 1))
        sections = []
        for i, day in enumerate(daily['time'][:3]):
            desc, day_icon = condition(daily['weather_code'][i])
            sections.append({'heading':('Today','Tomorrow')[i] if i < 2 else day, 'icon':day_icon, 'metrics':[
                {'label':'Conditions','value':desc},
                {'label':'High / low','value':f"{daily['temperature_2m_max'][i]:.0f}° / {daily['temperature_2m_min'][i]:.0f}°"}]})
        return Result('healthy', text, [
            {'label':'Feels like','value':f"{now['apparent_temperature']:.0f}",'unit':'°C'},
            {'label':'Wind','value':f"{now['wind_speed_10m']:.0f}",'unit':'km/h'}],
            {'hero':{'icon':icon,'title':f"{now['temperature_2m']:.0f}°C",'text':text,'place':config['place']},'sections':sections})
