import asyncio
import unittest
import httpx
from connectors import REGISTRY


def report(**changes):
    value = {'roots': 1, 'intermediates': 1, 'active_leaves': 12, 'expiring': 0, 'expired': 0, 'revoked': 2,
             'authorities': [{'kind': 'root', 'cn': 'Fixture Root', 'status': 'valid', 'days_left': 3000, 'crl_next': 'x', 'crl_overdue': False},
                             {'kind': 'intermediate', 'cn': 'Fixture Intermediate', 'status': 'valid', 'days_left': 900, 'crl_next': 'x', 'crl_overdue': False}],
             'attention': [], 'acme': {'enabled': True, 'ca': 'Fixture Intermediate'}}
    value.update(changes)
    return value


class HomeCATests(unittest.TestCase):
    def poll(self, summary=None, status=200):
        def handler(request):
            self.assertEqual((request.method, request.url.path), ('GET', '/api/summary'))
            self.assertEqual(request.headers['authorization'], 'Bearer fixture-token')
            self.assertNotIn('fixture-token', str(request.url))
            return httpx.Response(status, json=summary if summary is not None else report())
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['homeca'].poll(client, 'https://ca.example', {}, 'fixture-token')
        return asyncio.run(run())

    def rows(self, result):
        return [(i['title'], i['subtitle'], i['state']) for i in result.details['items']]

    def test_healthy_ca(self):
        result = self.poll()
        self.assertEqual((result.status, result.summary), ('healthy', 'HomeCA API available'))
        self.assertEqual({m['label']: m['value'] for m in result.metrics}, {
            'Certificates': '12', 'Expiring soon': '0', 'Expired': '0', 'Nearest CA expiry': '2.5 years'})
        self.assertEqual((result.details['items'], result.details['note']), ([], 'ACME is on, issuing from Fixture Intermediate'))

    def test_expired_and_manual_certificates_need_attention(self):
        result = self.poll(report(expired=1, expiring=1, attention=[
            {'cn': 'old.example', 'kind': 'leaf', 'status': 'expired', 'days_left': -3, 'acme': False},
            {'cn': 'soon.example', 'kind': 'leaf', 'status': 'expiring', 'days_left': 12, 'acme': False}]))
        self.assertEqual(result.status, 'degraded')
        self.assertEqual(self.rows(result), [('old.example', 'Certificate · renew it by hand', 'Expired 3 day(s) ago'),
                                             ('soon.example', 'Certificate · renew it by hand', 'Expires in 12 day(s)')])

    def test_acme_certificates_are_flagged_only_when_renewal_has_clearly_failed(self):
        result = self.poll(report(expiring=2, attention=[
            {'cn': 'renewing.example', 'kind': 'leaf', 'status': 'expiring', 'days_left': 20, 'acme': True},
            {'cn': 'stuck.example', 'kind': 'leaf', 'status': 'expiring', 'days_left': 5, 'acme': True}]))
        self.assertEqual(self.rows(result), [('stuck.example', 'Certificate · ACME is not renewing it', 'Expires in 5 day(s)')])

    def test_authorities_expiring_or_with_overdue_revocation_lists(self):
        authorities = report()['authorities']
        authorities[0] = {**authorities[0], 'days_left': 60}
        authorities[1] = {**authorities[1], 'crl_overdue': True}
        result = self.poll(report(authorities=authorities, attention=[{'cn': 'Fixture Root', 'kind': 'root', 'status': 'valid', 'days_left': 60, 'acme': False}]))
        self.assertEqual(self.rows(result), [('Fixture Root', 'Root CA', 'Expires in 60 day(s)'),
                                             ('Fixture Intermediate', 'Intermediate CA', 'Revocation list overdue')])  # no duplicate from attention
        self.assertEqual(result.metrics[3]['value'], '60 days')

    def test_acme_off_and_no_authorities(self):
        result = self.poll(report(authorities=[], acme={'enabled': False, 'ca': None}))
        self.assertEqual((result.metrics[3]['value'], result.details['note']), ('None', 'ACME is off.'))

    def test_rejected_token_or_wrong_shape_is_an_error(self):
        with self.assertRaises(httpx.HTTPStatusError): self.poll(status=401)
        with self.assertRaises(ValueError): self.poll({'unexpected': True})

if __name__ == '__main__':
    unittest.main()
