# HomeDash

A self-hosted homelab dashboard in the Home* design language. Browser links stay useful on their own; attach a connection when you want live status.

## Included in this first version

- Compact service cards with icons, descriptions, browser links, search and overflow menus.
- Named groups, group ordering, drag ordering within groups, and keyboard-accessible Move earlier/later actions.
- Private dashboards per user; local username/password accounts and optional OpenID Connect.
- Server settings for users and OIDC. Accepts an issuer or its full discovery URL; saved secrets are masked and retained.
- Bundled read-only connectors: HTTP availability, Home Assistant entity states, and Proxmox cluster overview.
- Shared backend polling, encrypted API credentials, verified TLS with optional private CA bundles, bounded responses, and stale-data indicators.

Docker, OPNsense and Synology are available as **link presets**, not live connectors yet. HomeKit, vacuum and heat-pump entities can be displayed through Home Assistant when that instance exposes them. This version does not control devices or run host commands.

## Run with a container

```sh
docker build -f Containerfile -t homedash:latest .
docker compose up -d
```

Open `http://127.0.0.1:8790` and create your first administrator account. No default password or deployment-specific hosts ship in this repository.

When using a reverse proxy, set the exact external URL before starting:

```sh
HOMEDASH_PUBLIC_URL=https://dashboard.example.net docker compose up -d
```

The example Compose binds loopback by default. Set `HOMEDASH_BIND` to a private interface address if a proxy on another host needs access. TLS terminates at your reverse proxy. `HOMEDASH_IMAGE` selects a published image instead of a local build. Run one Uvicorn worker per database; its background task owns the polling schedule.

## Add services and groups

1. Open **Groups** to create sections such as Infrastructure, Home, or Media.
2. Choose **Add service**. A preset can fill its name, description and icon; enter your own URL.
3. Select a group, icon and optionally a live connection.
4. **Open** launches the service in a new browser tab. The card's **⋯** menu contains Edit, ordering actions and Remove.
5. Drag cards within their group to reorder. Change a card's group through Edit. Groups have their own up/down ordering controls.

Removing a group keeps its cards under Ungrouped. Removing a connection keeps attached cards as ordinary links.

## Live connections

Add a connection from the **Connections** tab, then select it when editing a service card. Several cards can share one connection. Each user's connections and credentials remain private to that account.

The server checks each connection at its configured interval (15–3600 seconds; default 60). Browsers retrieve cached results every 15 seconds. **Refresh status** updates the browser from the cache; **Check connection** fetches a result when its interval is due, preventing repeated clicks from hammering a host. No polling occurs in the browser directly against your infrastructure.

Errors show Unavailable without exposing provider responses or credentials. The last successful metrics remain visible and are labelled stale. Checks time out, do not follow redirects, and use at most four concurrent network requests. A connection edit invalidates its prior snapshot.

### Website availability

Enter the HTTP or HTTPS endpoint to check. A 2xx or 3xx response means reachable; 4xx/5xx and network/TLS failures mean unavailable. Redirects are not followed and this check does not sign in to the destination. For large sites, prefer a small health endpoint. Credentials in URLs are rejected.

### Home Assistant

Use the HTTPS base URL of your Home Assistant instance and a long-lived access token. Enter up to eight entity IDs, separated by commas, for example:

```text
sensor.living_room_temperature, vacuum.roborock
```

The connector reads `/api/states/<entity_id>` and shows friendly names, states and units. It makes no service calls. Token permissions come from the Home Assistant account that issued it; use an appropriate dedicated account. Entities that report `unknown` or `unavailable` put the connection into Needs attention.

Reference: [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/).

### Proxmox VE

Use the HTTPS base URL, including port 8006 when appropriate. Supply the token in this form:

```text
user@realm!token-id=secret
```

Grant read-only audit access at the scope you want to display; privilege-separated tokens need suitable permissions for both the token and its user. The connector reads `/api2/json/cluster/resources` and shows nodes online, guests running, and node memory usage. API permissions determine which resources are visible.

Reference: [Proxmox VE API](https://pve.proxmox.com/wiki/Proxmox_VE_API).

### Private certificate authorities

Connection editors accept a public PEM CA bundle under **Private certificate authority**. Certificate verification stays enabled. API-token connections require HTTPS; put an internal HTTPS proxy in front of a service that only offers HTTP. The browser independently needs to trust the dashboard's certificate.

## Accounts and OIDC

The first account is an administrator. Account → Server settings → Users adds or removes other accounts. Users manage their own services, groups and connections. Administrators manage accounts and provider settings; the normal catalog API still returns only their own dashboard.

Under OAuth / OIDC, enter the issuer or discovery URL, client ID, secret and optional provider CA. Register the displayed callback URL with your provider. Blank secret fields retain the existing secret; the browser never receives the saved value. Local password login remains available. An exact preferred-username match links SSO to a local account; the configured provider is trusted to assert that identity. An unmatched SSO identity gets a private, non-administrator account.

Optional environment configuration is also supported with `HOMEDASH_OIDC_ISSUER`, `HOMEDASH_OIDC_CLIENT_ID`, `HOMEDASH_OIDC_CLIENT_SECRET_FILE`, `HOMEDASH_OIDC_CA_FILE`, and `HOMEDASH_OIDC_ALLOWED_USERS`. Mount referenced files separately. UI-saved settings take precedence.

## Data and backups

Persist `/data` (`HOMEDASH_DATA` outside containers). Back up **both** `homedash.db` and `secrets.key`; the key decrypts connection tokens and saved OIDC secrets. Restore them together. Use a SQLite-consistent backup or stop the container while copying. Treat these backups as sensitive.

The container runs unprivileged with a read-only root filesystem and writable data volume. No Docker socket, SSH keys or host agent is required.

## Connector extension contract

Connectors are trusted Python implementations bundled in `connectors/`. This is an extension foundation, not an untrusted plugin marketplace or hot-install mechanism.

Each connector supplies:

- `id`, `name`, `description`, `secret_label` and configuration `fields` for the UI.
- `validate(config)` returning normalized configuration or raising `ValueError`.
- Async `poll(client, base_url, config, secret)` returning a `Result(status, summary, metrics)`.

Statuses are `healthy` or `degraded`; the scheduler produces `pending` and `unavailable`. Each metric has `label`, `value` and optional `unit`. Register an instance in `REGISTRY`; the UI discovers it from `/api/plugins` automatically. Use the supplied HTTP client and bounded `fetch` helper. Do not log secrets, return raw errors, make state-changing requests, or bypass TLS. Add connector fixture tests and rebuild the image.

The core handles ownership, configuration forms, encryption, TLS trust, scheduling, caching and card rendering. Future connectors can be added without redesigning the dashboard.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HOMEDASH_PUBLIC_URL=http://127.0.0.1:8790 .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8790
.venv/bin/python -m unittest discover -s tests -q
node --check static/app.js
```

Tests use temporary databases and mocked connector APIs. They cover account boundaries, ordering, cascading deletes, secret retention, OIDC settings, caching, stale metrics and connector responses. Actual Home Assistant/Proxmox access requires deployment-specific URLs, tokens and certificate trust; no credentials ship in this project.
