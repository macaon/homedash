"""Read-only connection, state, temperatures, humidity and printer errors."""
import asyncio
import math


def reading(value, suffix):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    return f'{value:.0f}{suffix}'


def printer_section(printer, status):
    connected = status['connected']
    if not isinstance(connected, bool):
        raise ValueError('Expected printer connection state')
    temperatures = status.get('temperatures') or {}
    errors = status.get('hms_errors')
    state = str(status.get('state') or 'Unknown')
    names = {'RUNNING':'Printing', 'PREPARE':'Preparing', 'PAUSE':'Paused', 'IDLE':'Idle',
             'FINISH':'Finished', 'FAILED':'Failed', 'SLICING':'Slicing'}
    humidity = []
    for index, ams in enumerate(status.get('ams') or []):
        value = ams.get('humidity')
        if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 100:
            humidity.append((index + 1, reading(value, '%')))
    humidity_text = (humidity[0][1] if len(humidity) == 1 else
                     ' · '.join(f'AMS {index}: {value}' for index, value in humidity))
    if not humidity_text:
        humidity_text = 'Unavailable' if status.get('ams_exists') else 'Not fitted'
    if errors is None:
        error_text = 'Unknown'
    else:
        error_text = '; '.join(str(error.get('description') or error.get('code') or 'Printer error')[:250]
                               for error in errors[:3]) if errors else 'None'
        if len(errors) > 3:
            error_text += f'; +{len(errors)-3} more'
    return {'heading':str(printer.get('name') or status.get('name') or 'Printer')[:100], 'metrics':[
        {'label':'Connection', 'value':'Online' if connected else 'Offline'},
        {'label':'State', 'value':names.get(state, state.replace('_', ' ').capitalize()) if connected else 'Unavailable'},
        {'label':'Nozzle / bed', 'value':f'{reading(temperatures.get("nozzle"), "°C")} / {reading(temperatures.get("bed"), "°C")}' if connected else '—'},
        {'label':'AMS humidity', 'value':humidity_text if connected else '—'},
        {'label':'Errors', 'value':error_text if connected else 'Unknown'},
    ]}, not connected or bool(errors) or state == 'FAILED'


class Bambuddy:
    id = 'bambuddy'
    name = 'Bambuddy'
    description = 'Printer connection, state, nozzle/bed temperatures, AMS humidity and errors. Read-only.'
    secret_label = 'API key'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        headers = {'X-API-Key':secret}
        printers = await fetch(client, base + '/api/v1/printers/', headers)
        if not isinstance(printers, list) or len(printers) > 32:
            raise ValueError('Expected up to 32 printers')
        sections = []
        attention = 0
        for offset in range(0, len(printers), 4):
            batch = printers[offset:offset+4]
            ids = [p['id'] for p in batch]
            if any(type(ident) is not int or ident < 0 for ident in ids):
                raise ValueError('Invalid printer ID')
            statuses = await asyncio.gather(*(fetch(client, base + f'/api/v1/printers/{ident}/status', headers) for ident in ids))
            for printer, status in zip(batch, statuses):
                section, degraded = printer_section(printer, status)
                sections.append(section)
                attention += degraded
        return Result('degraded' if attention else 'healthy',
                      f'{attention} printer(s) need attention' if attention else 'Bambuddy API available', [],
                      {'sections':sections, 'empty':'No printers configured.'})
