"""Refresh bundled Lucide assets from the pinned, integrity-checked npm release."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request
import xml.etree.ElementTree as ET

VERSION='1.64.0'
INTEGRITY='UxWmMteOXbKHI5E4f2qVtxCAAsI9gsInYK1lNw0nrOA0w4FkJeOVZ+UOV6aGA2377FZe3qyiFYq3lqjBKZ6OuA=='

def main():
    raw=urllib.request.urlopen(f'https://registry.npmjs.org/lucide-static/-/lucide-static-{VERSION}.tgz',timeout=30).read()
    assert base64.b64encode(hashlib.sha512(raw).digest()).decode()==INTEGRITY, 'Package integrity mismatch'
    out=Path(__file__).resolve().parents[1]/'static/lucide'
    out.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        tags=json.load(archive.extractfile('package/tags.json'))
        names=sorted(Path(m.name).stem for m in archive.getmembers() if m.name.startswith('package/icons/') and m.name.endswith('.svg'))
        catalog={'version':VERSION,'icons':[{'name':name,'tags':tags.get(name,[])} for name in names]}
        ET.register_namespace('', 'http://www.w3.org/2000/svg')
        sprite=ET.Element('{http://www.w3.org/2000/svg}svg')
        for name in names:
            source=ET.fromstring(archive.extractfile('package/icons/'+name+'.svg').read())
            symbol=ET.SubElement(sprite,'{http://www.w3.org/2000/svg}symbol',id=name,viewBox='0 0 24 24')
            symbol.extend(list(source))
        (out/'sprite.svg').write_bytes(ET.tostring(sprite,encoding='utf-8',xml_declaration=True))
        for src,dst in [('LICENSE','LICENSE')]:
            (out/dst).write_bytes(archive.extractfile('package/'+src).read())
        (out/'catalog.json').write_text(json.dumps(catalog,separators=(',',':'))+'\n')
    print(f'Bundled {len(names)} Lucide icons, version {VERSION}')

if __name__=='__main__':main()
