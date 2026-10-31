import asyncio
import unittest
import httpx
from connectors import REGISTRY
from connectors.bambuddy import printer_section


class BambuddyTests(unittest.TestCase):
    def status(self, **changes):
        return {'connected':True,'state':'IDLE','temperatures':{'nozzle':25.7,'bed':22.9},
                'ams_exists':True,'ams':[{'humidity':35}], 'hms_errors':[], **changes}

    def test_requested_fields_only(self):
        row, degraded=printer_section({'name':'P1S'},self.status(progress=85, total_layers=96))
        self.assertFalse(degraded)
        self.assertEqual([m['label'] for m in row['metrics']],['Connection','State','Nozzle / bed','AMS humidity','Errors'])
        self.assertEqual([m['value'] for m in row['metrics']],['Online','Idle','26°C / 23°C','35%','None'])
        self.assertNotIn('progress',str(row))

    def test_disconnected_printer_does_not_present_retained_readings_as_current(self):
        row,degraded=printer_section({},self.status(connected=False))
        self.assertTrue(degraded)
        self.assertEqual([m['value'] for m in row['metrics']],['Offline','Unavailable','—','—','Unknown'])

    def test_missing_sensors_and_no_ams(self):
        row,_=printer_section({},self.status(temperatures={},ams=[],ams_exists=False))
        self.assertEqual(row['metrics'][2]['value'],'— / —')
        self.assertEqual(row['metrics'][3]['value'],'Not fitted')

    def test_multiple_ams_and_unknown_readings(self):
        row,_=printer_section({},self.status(ams=[{'humidity':35},{'humidity':40},{'humidity':-1}]))
        self.assertEqual(row['metrics'][3]['value'],'AMS 1: 35% · AMS 2: 40%')
        row,_=printer_section({},self.status(ams=[{'humidity':None}]))
        self.assertEqual(row['metrics'][3]['value'],'Unavailable')

    def test_errors_degrade_and_are_visible(self):
        row,degraded=printer_section({},self.status(hms_errors=[{'description':'Filament ran out'}]))
        self.assertTrue(degraded)
        self.assertEqual(row['metrics'][4]['value'],'Filament ran out')

    def poll(self, printers, fail=False):
        def handler(request):
            self.assertEqual(request.method,'GET')
            self.assertEqual(request.headers['X-API-Key'],'fixture-secret')
            self.assertNotIn('fixture-secret',str(request.url))
            if fail: return httpx.Response(403)
            return httpx.Response(200,json=printers if request.url.path=='/api/v1/printers/' else self.status())
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['bambuddy'].poll(client,'https://bambu.example',{},'fixture-secret')
        return asyncio.run(run())

    def test_multiple_printers_get_separate_sections(self):
        result=self.poll([{'id':1,'name':'P1S'},{'id':2,'name':'A1'}])
        self.assertEqual(result.status,'healthy')
        self.assertEqual([s['heading'] for s in result.details['sections']],['P1S','A1'])
        self.assertEqual(result.metrics,[])

    def test_empty_printer_list(self):
        result=self.poll([])
        self.assertEqual(result.details['sections'],[])
        self.assertEqual(result.details['empty'],'No printers configured.')

    def test_permissions_fail_instead_of_showing_empty(self):
        with self.assertRaises(httpx.HTTPStatusError):self.poll([],fail=True)
