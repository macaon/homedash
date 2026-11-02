"""Read-only certificate health from HomeCA's /api/summary (a Read-level API token is enough)."""
CA_WARNING = 90          # days before a CA expires
ACME_RENEWAL_FAILED = 7  # ACME clients renew at two-thirds of a certificate's life; this close to expiry means they have not


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected a non-negative integer')
    return value


def days(value):
    if type(value) is not int:
        raise ValueError('Expected a number of days')
    return value


def span(value):
    return f'{value / 365:.1f} years' if value >= 730 else f'{value:,} days'


def left(value):
    return f'Expired {-value:,} day(s) ago' if value < 0 else f'Expires in {value:,} day(s)'


class HomeCA:
    id = 'homeca'
    name = 'HomeCA'
    description = 'Certificate counts, CA expiry, revocation list freshness and certificates that need attention. Needs a Read API token from HomeCA.'
    secret_label = 'API token'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        report = await fetch(client, base + '/api/summary', {'Authorization': f'Bearer {secret}'})
        if not isinstance(report, dict) or not isinstance(report.get('authorities'), list) or not isinstance(report.get('attention'), list):
            raise ValueError('Expected a certificate summary')
        items = []
        for authority in report['authorities'][:50]:
            name, kind = str(authority['cn'])[:250], 'Root CA' if authority['kind'] == 'root' else 'Intermediate CA'
            remaining = days(authority['days_left'])
            if remaining < CA_WARNING:
                items.append({'title': name, 'subtitle': kind, 'state': left(remaining)})
            if authority.get('crl_overdue') is True:
                items.append({'title': name, 'subtitle': kind, 'state': 'Revocation list overdue'})
        for cert in report['attention'][:50]:
            if cert['kind'] != 'leaf':
                continue  # authorities are handled above
            remaining, issued_by_acme = days(cert['days_left']), cert.get('acme') is True
            if issued_by_acme and remaining > ACME_RENEWAL_FAILED:
                continue  # inside ACME's renewal window; the client will renew it
            items.append({'title': str(cert['cn'])[:250], 'subtitle': 'Certificate' + (' · ACME is not renewing it' if issued_by_acme else ' · renew it by hand'),
                          'state': left(remaining)})
        remaining = [days(a['days_left']) for a in report['authorities'] if a.get('status') != 'expired']
        acme = report.get('acme') or {}
        metrics = [{'label': 'Certificates', 'value': f'{count(report["active_leaves"]):,}'},
                   {'label': 'Expiring soon', 'value': f'{count(report["expiring"]):,}'},
                   {'label': 'Expired', 'value': f'{count(report["expired"]):,}'},
                   {'label': 'Nearest CA expiry', 'value': span(min(remaining)) if remaining else 'None'}]
        details = {'heading': 'Needs attention', 'items': items[:100], 'empty': 'Nothing needs attention.', 'more_label': 'More items',
                   'note': ('ACME is on, issuing from ' + str(acme.get('ca'))[:100]) if acme.get('enabled') is True else 'ACME is off.'}
        return Result('degraded' if items else 'healthy', f'{len(items)} item(s) need attention' if items else 'HomeCA API available', metrics, details)
