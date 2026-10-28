# HomeDash

A self-hosted homelab dashboard in the Home* design language. Browser links stay useful on their own; attach a connection when you want live status.

## Included in this first version

- Compact service cards with icons, descriptions, browser links, search and overflow menus.
- Named groups, group ordering, drag ordering within groups, and keyboard-accessible Move earlier/later actions.
- Private dashboards per user; local username/password accounts and optional OpenID Connect.
- Server settings for users and OIDC. Accepts an issuer or its full discovery URL; saved secrets are masked and retained.
- Bundled read-only connectors: HTTP availability, Home Assistant entity states, and Proxmox cluster overview.
- Shared backend polling, encrypted API credentials, verified TLS with shared server certificate trust, bounded responses, and stale-data indicators.

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
3. Select a group and icon. Under **Service type**, choose Link only, Website availability, Home Assistant, Proxmox VE, or another installed service definition. Configure its check directly in the same form.
4. **Open** launches the service in a new browser tab. The card's **⋯** menu contains Edit, ordering actions and Remove.
5. Drag cards within their group to reorder. Change a card's group through Edit. Groups have their own up/down ordering controls.

Removing a group keeps its cards under Ungrouped. Removing a saved check keeps attached cards as ordinary links.

## Live connections

Configure checks and live metrics directly in **Add service** or **Edit service**. The check URL initially follows the service URL; API services default to its origin. Select the service type, enter any required entity IDs or token, and expand advanced options for the check interval. Saving the service and its check is atomic.

Basic availability uses a small status dot beside the hostname; hover or focus it for Online/Offline, response details and the last check time. Live API integrations retain their metrics.

Saved checks can be reused from the service-type dropdown in Add/Edit service. Unattached checks remain saved but are not polled. Editing a shared check from a service creates a separate copy, keeping the other services unchanged. Switching to Link only detaches the check from that card; the detached check stays available for reuse. Each user's connections and credentials remain private to that account.

The server checks each connection at its configured interval (15–3600 seconds; default 60). Browsers retrieve cached results every 15 seconds. **Refresh status** updates the browser from the cache; checks run only while at least one service uses them. No polling occurs in the browser directly against your infrastructure.

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

Administrators manage shared certificate trust under **settings cog → Trust**. Name an upload and select one or several public PEM/DER certificate files; PEM bundles are supported too. Upload a private CA root once and all service checks and OIDC logins can trust certificates signed by it. Duplicate certificates are ignored. The list shows each certificate's subject, expiry and SHA-256 fingerprint, with a removal action.

Certificate fields are no longer part of the service or OIDC editor. Untrusted certificate failures direct users to the Trust tab. Adding or removing trust automatically invalidates cached results and triggers fresh checks. Standard public roots remain enabled; hostname and expiry verification remain enforced. Previously configured per-service/provider CA bundles are retained for compatibility, while new services use shared trust.

API-token connections require HTTPS; put an internal HTTPS proxy in front of a service that only offers HTTP. This trust store controls HomeDash's outbound requests; browsers independently need to trust the dashboard's own certificate.

## Accounts and OIDC

The first account is an administrator. Settings cog → Users adds or removes other accounts. Users manage their own services, groups and connections. Administrators manage accounts and provider settings; the normal catalog API still returns only their own dashboard.

Under OAuth / OIDC, enter the issuer or discovery URL, client ID, secret and upload any private provider CA in Trust. Register the displayed callback URL with your provider. Blank secret fields retain the existing secret; the browser never receives the saved value. Local password login remains available. An exact preferred-username match links SSO to a local account; the configured provider is trusted to assert that identity. An unmatched SSO identity gets a private, non-administrator account.

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

### JSON service definitions: no rebuild or restart

For APIs that only need a GET request and a few scalar values, put a definition in `/data/connectors/*.json`, or set `HOMEDASH_CONNECTORS_DIR` to a mounted directory. There is no JSON editor or importer in the GUI. Definitions are maintained alongside deployment/code files.

See [examples/service-status.json](examples/service-status.json). For that example, the service API would return:

```json
{"sensors":{"temperature":21.5},"uptime_seconds":12345}
```

Definitions specify a unique `custom_` ID, a display name, a relative API path, authentication (`none`, `bearer`, or `header`), and up to eight metrics. `header` authentication can set a credential header such as `X-API-Key` using the `header` field. Metric `pointer` values use JSON Pointer syntax, including `~1` for `/` and `~0` for `~`; they can address array elements. Values must be scalars. Do not put tokens or deployment hostnames in definitions: the service editor supplies the base URL and encrypted token.

The server checks this directory every five seconds. Valid changes replace the definition set atomically; invalid edits leave the last working set active. Refresh the dashboard to see a newly added type in the service editor. Existing checks use changed definitions on their next scheduled check. Removing a definition makes its existing checks unavailable until the definition is restored. Keep IDs stable when changing labels or mappings.

For an easily editable host directory, create it and add a read-only bind mount to Compose:

```yaml
volumes:
  - homedash-data:/data
  - ./service-definitions:/data/connectors:ro
```

Write updates to a temporary non-JSON filename and rename into place when complete. No Python expressions, scripts, custom methods, cross-origin URLs or arbitrary frontend code are accepted. APIs needing a new authentication flow, discovery or protocol logic still need a Python connector and an image update.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HOMEDASH_PUBLIC_URL=http://127.0.0.1:8790 .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8790
.venv/bin/python -m unittest discover -s tests -q
node --check static/app.js
```

Tests use temporary databases and mocked connector APIs. They cover account boundaries, ordering, cascading deletes, secret retention, OIDC settings, caching, stale metrics and connector responses. Actual Home Assistant/Proxmox access requires deployment-specific URLs, tokens and certificate trust; no credentials ship in this project.

The settings cog sits immediately left of the username and is visible only to administrators. Theme, password and log out stay in the account menu.

### Default appearance

Settings cog → **User Interface** sets the server default to Auto, Light or Dark. Auto follows the operating system and is the default on fresh installations. The dashboard and login page both follow this setting. Appearance controls are kept in settings rather than the account dropdown.
