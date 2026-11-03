"""Current conditions and a three-day forecast from Open-Meteo (no API key)."""

# WMO weather interpretation codes, grouped to keep the table short.
CODES = {0:'Clear',1:'Mostly clear',2:'Partly cloudy',3:'Overcast',45:'Fog',48:'Fog',
         51:'Light drizzle',53:'Drizzle',55:'Heavy drizzle',56:'Freezing drizzle',57:'Freezing drizzle',
         61:'Light rain',63:'Rain',65:'Heavy rain',66:'Freezing rain',67:'Freezing rain',
         71:'Light snow',73:'Snow',75:'Heavy snow',77:'Snow grains',
         80:'Light showers',81:'Showers',82:'Heavy showers',85:'Snow showers',86:'Snow showers',
         95:'Thunderstorm',96:'Thunderstorm with hail',99:'Thunderstorm with hail'}


def condition(code):
    return CODES.get(code, 'Unknown')


class Weather:
    id = 'weather'
    name = 'Weather'
    description = 'Current conditions and a three-day forecast from Open-Meteo. Shown in the header. Your coordinates are sent to the API URL below; use a self-hosted instance if you prefer.'
    secret_label = ''
    placement = 'header'
    default_url = 'https://api.open-meteo.com'
    fields = [{'key':'latitude','label':'Latitude','placeholder':'59.33','help':'Decimal degrees, −90 to 90.'},
              {'key':'longitude','label':'Longitude','placeholder':'18.07','help':'Decimal degrees, −180 to 180.'}]

    def validate(self, config):
        try:
            lat, lon = float(config.get('latitude', '')), float(config.get('longitude', ''))
        except ValueError:
            raise ValueError('Enter latitude and longitude as decimal numbers') from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('Latitude must be within ±90 and longitude within ±180')
        return {'latitude': f'{lat:g}', 'longitude': f'{lon:g}'}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        config = self.validate(config)
        data = await fetch(client, f"{base}/v1/forecast?latitude={config['latitude']}&longitude={config['longitude']}"
            '&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m'
            '&daily=weather_code,temperature_2m_max,temperature_2m_min&forecast_days=3&timezone=auto')
        now, daily = data['current'], data['daily']
        text = condition(now['weather_code'])
        sections = []
        for i, day in enumerate(daily['time'][:3]):
            sections.append({'heading':('Today','Tomorrow')[i] if i < 2 else day, 'metrics':[
                {'label':'Conditions','value':condition(daily['weather_code'][i])},
                {'label':'High / low','value':f"{daily['temperature_2m_max'][i]:.0f}° / {daily['temperature_2m_min'][i]:.0f}°"}]})
        return Result('healthy', text, [
            {'label':'Temperature','value':f"{now['temperature_2m']:.0f}",'unit':'°C'},
            {'label':'Feels like','value':f"{now['apparent_temperature']:.0f}",'unit':'°C'},
            {'label':'Wind','value':f"{now['wind_speed_10m']:.0f}",'unit':'km/h'}], {'sections':sections})
