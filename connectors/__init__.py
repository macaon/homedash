"""Trusted, bundled read-only connectors. Register new implementations in REGISTRY."""
from dataclasses import dataclass, field
from typing import Protocol
import re
import asyncio
import ipaddress
import ssl
from urllib.parse import urlsplit
import httpx

MAX_BODY = 2 * 1024 * 1024

@dataclass
class Result:
    status: str
    summary: str
    metrics: list[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)

class Connector(Protocol):
    id: str
    name: str
    description: str
    secret_label: str
    fields: list[dict]
    def validate(self, config: dict) -> dict: ...
    async def poll(self, client: httpx.AsyncClient, base: str, config: dict, secret: str) -> Result: ...

async def fetch(client, url, headers=None, *, as_json=True, raw=False):
    async with client.stream('GET', url, headers=headers or {}) as response:
        if as_json or raw or response.status_code >= 400:
            response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise ValueError('Response too large')
        if raw:
            return bytes(body)
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

def validate_ping_target(value):
    value=value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if len(value)>253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?', value):
        raise ValueError('Enter a hostname or IPv4/IPv6 address, without a URL or port')
    if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part) for part in value.rstrip('.').split('.')):
        raise ValueError('Enter a valid hostname or IP address')
    return value

class Ping:
    id = 'ping'
    name = 'Ping'
    description = 'Send three ICMP echo requests from the HomeDash server. Shows average round-trip latency and packet loss. Hosts that block ICMP may appear offline.'
    secret_label = ''
    fields = []
    def validate(self, config):
        return {}
    async def poll(self, client, base, config, secret):
        target=validate_ping_target(base)
        process=await asyncio.create_subprocess_exec(
            'ping', '-n', '-c', '3', '-i', '0.2', '-W', '2', '-w', '7', '--', target,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
        try:
            output,_=await asyncio.wait_for(process.communicate(),timeout=9)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        text=output.decode(errors='replace')
        loss=re.search(r'(\d+(?:\.\d+)?)% packet loss',text)
        timing=re.search(r'= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms',text)
        if process.returncode not in (0,1) or not loss:
            return Result('unavailable','Ping failed. Check the hostname, DNS and ICMP permissions.')
        percent=float(loss[1])
        metrics=[{'label':'Packet loss','value':f'{percent:g}','unit':'%'}]
        if timing:
            metrics.insert(0,{'label':'Latency','value':f'{float(timing[2]):.2f}','unit':'ms'})
        status='unavailable' if percent==100 else 'degraded' if percent else 'healthy'
        return Result(status,'No ping replies (ICMP may be blocked)' if percent==100 else f'{percent:g}% packet loss',metrics)

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

from .audiobookshelf import Audiobookshelf
from .audiobookrequest import AudioBookRequest
from .bambuddy import Bambuddy
from .homepod import HomePod
from .immich import Immich
from .paperless import Paperless
from .komga import Komga
from .calibreweb import CalibreWeb
from .gitea import Gitea
from .mylar import Mylar
from .homeca import HomeCA
from .bookstack import BookStack
from .weather import Weather
from .transit import Transit
from .departures import Departures

REGISTRY = {plugin.id:plugin for plugin in (HTTP(), Ping(), HomeAssistant(), Proxmox(), Audiobookshelf(), AudioBookRequest(), Bambuddy(), BookStack(), Immich(), Paperless(), Komga(), CalibreWeb(), Gitea(), Mylar(), HomeCA(), HomePod(), Weather(), Transit(), Departures())}

def manifest():
    return [{'id':p.id,'name':p.name,'description':p.description,'secret_label':p.secret_label,'fields':p.fields,'placement':getattr(p,'placement','card'),'default_url':getattr(p,'default_url',''),'search':getattr(p,'search_ui',None),'notice':getattr(p,'notice','')} for p in list(REGISTRY.values())]

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

# JSON definitions describe a bounded GET + metric mapping, never executable code.
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

class JSONMetric(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1,max_length=80)
    pointer: str = Field(min_length=1,max_length=256)
    unit: str = Field(default='',max_length=20)
    @field_validator('pointer')
    @classmethod
    def pointer_valid(cls,value):
        if not value.startswith('/') or re.search(r'~(?![01])',value):
            raise ValueError('Use a JSON Pointer such as /data/temperature')
        return value

class JSONDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^custom_[a-z0-9_]{2,40}$')
    name: str = Field(min_length=1,max_length=80)
    description: str = Field(default='Read live metrics from a JSON API.',max_length=500)
    path: str = Field(min_length=1,max_length=1024)
    auth: Literal['none','bearer','header'] = 'none'
    header: str = Field(default='X-API-Key',pattern=r'^[A-Za-z][A-Za-z0-9-]{0,63}$')
    metrics: list[JSONMetric] = Field(min_length=1,max_length=8)
    @field_validator('path')
    @classmethod
    def path_valid(cls,value):
        parsed=urlsplit(value)
        if (not value.startswith('/') or value.startswith('//') or parsed.scheme or parsed.netloc
                or parsed.fragment or '\\' in value or any(ord(c)<33 for c in value)):
            raise ValueError('Path must stay on the configured service origin')
        return value
    @field_validator('header')
    @classmethod
    def header_valid(cls,value):
        if value.lower() in ('host','cookie','content-length','connection','transfer-encoding','proxy-authorization'):
            raise ValueError('Unsupported credential header')
        return value

class JSONConnector:
    fields=[]
    def __init__(self,definition):
        self.definition=definition
        self.id=definition.id
        self.name=definition.name
        self.description=definition.description
        self.secret_label='API token' if definition.auth!='none' else ''
    def validate(self,config):
        return {}
    async def poll(self,client,base,config,secret):
        definition=self.definition
        headers={}
        if definition.auth=='bearer':headers['Authorization']='Bearer '+secret
        if definition.auth=='header':headers[definition.header]=secret
        document=await fetch(client,base+definition.path,headers)
        metrics=[]
        for metric in definition.metrics:
            value=document
            for segment in metric.pointer[1:].split('/'):
                segment=segment.replace('~1','/').replace('~0','~')
                value=value[int(segment)] if isinstance(value,list) else value[segment]
            if not isinstance(value,(str,int,float,bool)) and value is not None:
                raise ValueError('Metric must be a scalar')
            metrics.append({'label':metric.label,'value':str(value)[:100] if value is not None else '—','unit':metric.unit})
        return Result('healthy','Live metrics available',metrics)

JSON_SIGNATURE=None
JSON_IDS=set()

def load_json_connectors(directory):
    global JSON_SIGNATURE, JSON_IDS
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    paths=sorted(directory.glob('*.json'))
    signature=(str(directory),tuple((p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in paths))
    if signature==JSON_SIGNATURE:
        return False
    loaded={}
    for path in paths:
        if path.stat().st_size>65536:
            raise ValueError(f'Connector definition too large: {path.name}')
        try:
            definition=JSONDefinition.model_validate_json(path.read_text())
        except ValueError:
            raise ValueError(f'Invalid connector definition: {path.name}') from None
        if (definition.id in REGISTRY and definition.id not in JSON_IDS) or definition.id in loaded:
            raise ValueError(f'Duplicate connector ID in {path.name}')
        loaded[definition.id]=JSONConnector(definition)
    for ident in JSON_IDS-set(loaded):
        REGISTRY.pop(ident,None)
    REGISTRY.update(loaded)
    JSON_IDS=set(loaded)
    JSON_SIGNATURE=signature
    return True
