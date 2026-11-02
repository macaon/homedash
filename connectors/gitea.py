"""Read-only repository, issue and organization totals for Gitea (and Forgejo). Only totals are read:
every request asks for one item and uses the X-Total-Count header. Deliberately no administrator endpoints:
that scope can read user emails and the runner registration token, which a dashboard token must not hold."""
import asyncio
import httpx

QUERIES = (('repos', '/api/v1/repos/search?limit=1'),
           ('issues', '/api/v1/repos/issues/search?state=open&type=issues&limit=1'),
           ('pulls', '/api/v1/repos/issues/search?state=open&type=pulls&limit=1'),
           ('orgs', '/api/v1/orgs?limit=1'))


def total(response):
    response.raise_for_status()
    value = response.headers.get('x-total-count', '')
    if not value.isdigit():
        raise ValueError('Gitea did not report a total')
    return int(value)


class Gitea:
    id = 'gitea'
    name = 'Gitea'
    description = 'Repository, open issue, pull request and organization totals. Needs an access token with only read:repository, read:issue and read:organization. Never give it administrator access.'
    secret_label = 'Access token'
    fields = []

    def validate(self, config):
        return {}

    async def poll(self, client, base, config, secret):
        from . import Result
        headers = {'Authorization': f'token {secret}'}
        async def get(path):
            return await client.get(base + path, headers=headers)
        responses = await asyncio.gather(*(get(path) for _, path in QUERIES))
        counts = {key: total(response) for (key, _), response in zip(QUERIES, responses)}
        metrics = [{'label': 'Repositories', 'value': f'{counts["repos"]:,}'},
                   {'label': 'Open issues', 'value': f'{counts["issues"]:,}'},
                   {'label': 'Open pull requests', 'value': f'{counts["pulls"]:,}'},
                   {'label': 'Organizations', 'value': f'{counts["orgs"]:,}'}]
        return Result('healthy', 'Gitea API available', metrics)
