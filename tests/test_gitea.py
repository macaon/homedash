import asyncio
import unittest
import httpx
from connectors import REGISTRY

TOTALS = {'/api/v1/repos/search': 61, '/api/v1/orgs': 1}


class GiteaTests(unittest.TestCase):
    def poll(self, status=200, header=True):
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['authorization'], 'token fixture-token')
            self.assertNotIn('fixture-token', str(request.url))
            self.assertEqual(request.url.params['limit'], '1')
            if status != 200:
                return httpx.Response(status)
            path = request.url.path
            self.assertNotIn('/admin', path)  # never an administrator endpoint
            total = {'issues': 5, 'pulls': 1}.get(request.url.params.get('type')) if path.endswith('/issues/search') else TOTALS[path]
            return httpx.Response(200, json=[{'name': 'secret-repo'}], headers={'x-total-count': str(total)} if header else {})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['gitea'].poll(client, 'https://git.example', {}, 'fixture-token')
        return asyncio.run(run())

    def test_totals(self):
        result = self.poll()
        self.assertEqual((result.status, {m['label']: m['value'] for m in result.metrics}), ('healthy', {
            'Repositories': '61', 'Open issues': '5', 'Open pull requests': '1', 'Organizations': '1'}))
        self.assertNotIn('secret-repo', str(result))

    def test_rejected_token_and_missing_totals_are_errors(self):
        with self.assertRaises(httpx.HTTPStatusError): self.poll(status=401)
        with self.assertRaises(ValueError): self.poll(header=False)

if __name__ == '__main__':
    unittest.main()
