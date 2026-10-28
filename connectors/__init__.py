"""Trusted, bundled read-only connectors. Register new implementations in REGISTRY."""
from dataclasses import dataclass, field
from typing import Protocol
import re
import ssl
from urllib.parse import urlsplit
import httpx

MAX_BODY = 2 * 1024 * 1024

@dataclass
class Result:
    status: str
    summary: str
    metrics: list[dict] = field(default_factory=list)

class Connector(Protocol):
    id: str
    name: str
    description: str
    secret_label: str
    fields: list[dict]
    def validate(self, config: dict) -> dict: ...
    async def poll(self, client: httpx.AsyncClient, base: str, config: dict, secret: str) -> Result: ...

async def fetch(client, url, headers=None, *, as_json=True):
    async with client.stream('GET', url, headers=headers or {}) as response:
        if as_json or response.status_code >= 400:
            response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise ValueError('Response too large')
        if as_json:
            import json
            return json.loads(body)
        return response.status_code

class HTTP:
    id = 'http'
    name = 'Website availability'
    description = 'Check an HTTP endpoint. Redirects indicate reachable; they are not followed.'
    secret_label = ''
    fields = []
    def validate(self, config):
        return {}
    async def poll(self, client, base, config, secret):
        code = await fetch(client, base, as_json=False)
        return Result('healthy', f'HTTP {code}', [{'label':'Response','value':str(code)}])

class HomeAssistant:
    id = 'homeassistant'
    name = 'Home Assistant'
    description = 'Read selected entity states using a long-lived access token.'
    secret_label = 'Long-lived access token'
    fields = [{'key':'entities','label':'Entity IDs','placeholder':'sensor.living_room_temperature, vacuum.roborock','help':'Comma separated, up to 8 entities. Read-only state display.'}]
    def validate(self, config):
        entities = [v.strip() for v in str(config.get('entities','')).split(',') if v.strip()]
        if not 1 <= len(entities) <= 8 or any(not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+', v) for v in entities):
            raise ValueError('Enter 1–8 valid Home Assistant entity IDs')
        return {'entities':', '.join(dict.fromkeys(entities))}
    async def poll(self, client, base, config, secret):
        metrics=[]
        for entity in config['entities'].split(', '):
            value = await fetch(client, base+'/api/states/'+entity, {'Authorization':'Bearer '+secret})
            attributes=value.get('attributes') or {}
            metrics.append({'label':str(attributes.get('friendly_name',entity))[:100], 'value':str(value['state'])[:100], 'unit':str(attributes.get('unit_of_measurement',''))[:20]})
        unavailable=sum(m['value'] in ('unavailable','unknown') for m in metrics)
        return Result('degraded' if unavailable else 'healthy', f'{unavailable} unavailable' if unavailable else f'{len(metrics)} entities connected', metrics)

class Proxmox:
    id = 'proxmox'
    name = 'Proxmox VE'
    description = 'Cluster overview using an API token with read-only audit permissions.'
    secret_label = 'API token (user@realm!token-id=secret)'
    fields = []
    def validate(self, config):
        return {}
    async def poll(self, client, base, config, secret):
        response = await fetch(client, base+'/api2/json/cluster/resources', {'Authorization':'PVEAPIToken='+secret})
        resources=response['data']
        nodes=[v for v in resources if v.get('type')=='node']
        guests=[v for v in resources if v.get('type') in ('qemu','lxc')]
        online=sum(v.get('status')=='online' for v in nodes)
        running=sum(v.get('status')=='running' for v in guests)
        return Result('healthy' if nodes and online==len(nodes) else 'degraded', f'{online}/{len(nodes)} nodes online', [
            {'label':'Nodes online','value':f'{online} / {len(nodes)}'},
            {'label':'Guests running','value':f'{running} / {len(guests)}'},
            {'label':'Memory used','value':str(round(sum(v.get('mem',0) for v in nodes)/1024**3,1)), 'unit':'GiB'}])

REGISTRY = {plugin.id:plugin for plugin in (HTTP(), HomeAssistant(), Proxmox())}

def manifest():
    return [{'id':p.id,'name':p.name,'description':p.description,'secret_label':p.secret_label,'fields':p.fields} for p in REGISTRY.values()]

def validate_url(value):
    parsed=urlsplit(value.strip())
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Use an HTTP or HTTPS URL without embedded credentials or a fragment')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('Invalid port')
    return value.strip()

def client(ca_pem):
    context=ssl.create_default_context()
    if ca_pem:
        context.load_verify_locations(cadata=ca_pem)
    return httpx.AsyncClient(verify=context,timeout=httpx.Timeout(8),follow_redirects=False,trust_env=False)
