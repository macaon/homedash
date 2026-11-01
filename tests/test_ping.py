import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from connectors import REGISTRY, validate_ping_target

class PingTests(unittest.TestCase):
    def test_targets(self):
        for value in ('host.example.net','192.0.2.1','::1'):
            self.assertEqual(validate_ping_target(value),value)
        for value in ('-c5','https://host','host:22','host;id','host\nother','a..b'):
            with self.assertRaises(ValueError):validate_ping_target(value)

    def result(self,loss,code=0,timing=True):
        proc=AsyncMock();proc.returncode=code
        proc.communicate.return_value=(f'3 packets transmitted, 3 received, {loss}% packet loss\n'.encode()+(b'rtt min/avg/max/mdev = 0.010/1.234/2.000/0.100 ms\n' if timing else b''),None)
        async def run():
            with patch('connectors.asyncio.create_subprocess_exec',return_value=proc) as spawn:
                result=await REGISTRY['ping'].poll(None,'::1',{},'')
                self.assertEqual(spawn.call_args.args[-2:],('--','::1'))
                return result
        return asyncio.run(run())

    def test_results(self):
        result=self.result(0)
        self.assertEqual(result.status,'healthy')
        self.assertEqual(result.metrics[0],{'label':'Latency','value':'1.23','unit':'ms'})
        self.assertEqual(self.result(33.333).status,'degraded')
        self.assertEqual(self.result(100,1,False).status,'unavailable')
        self.assertEqual(self.result(0,2,False).status,'unavailable')
