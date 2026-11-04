# HomeDash

A self-hosted homelab dashboard. Each service is a card that links to it; attach a connection to a card when you want live status such as availability, playback sessions or cluster health. Dashboards are private per user, and it runs as a single container.

## Features

- Compact service cards with icons, descriptions, browser links, search and overflow menus.
- Named groups, group ordering, drag ordering within groups, and keyboard-accessible Move earlier/later actions.
- Private dashboards per user; local username/password accounts and optional OpenID Connect.
- Server settings for users and OIDC. Accepts an issuer or its full discovery URL; saved secrets are masked and retained.
- Bundled read-only connectors: HTTP availability, Home Assistant entity states, and Proxmox cluster overview.
- Shared backend polling, encrypted API credentials, verified TLS with shared server certificate trust, bounded responses, and stale-data indicators.

Docker, OPNsense and Synology are available as **link presets**, not live connectors yet. HomeKit, vacuum and heat-pump entities can be displayed through Home Assistant when that instance exposes them. This version does not control devices or run host commands.

## Run with a container

```sh
cp .env.example .env     # optional: every setting has a default
docker compose up --build -d
```

Open `http://127.0.0.1:8790` and create your first administrator account. No default password or deployment-specific hosts ship in this repository. Podman Compose works too.

Compose builds the image from the `Containerfile`. Run it with `--build` again after pulling new source, otherwise it keeps reusing the existing image.

When using a reverse proxy, set the exact external URL in `.env` before starting:

```sh
HOMEDASH_PUBLIC_URL=https://dashboard.example.net
```

The example Compose binds loopback by default. Set `HOMEDASH_BIND` to a private interface address if a proxy on another host needs access. TLS terminates at your reverse proxy. **Sign-in protection.** After 10 failed sign-ins for an account from one source within five minutes, further attempts get HTTP 429 until the window passes. Behind a reverse proxy set `HOMEDASH_TRUSTED_PROXIES` to the proxy's address or network (for example `172.28.0.1`) so the limit sees the real client instead of the proxy; without it everyone behind the proxy shares one limit. Forwarded headers are believed only from those addresses. With Docker or Podman the proxy appears as the network's gateway address (for example `172.18.0.1`), not the host's LAN IP. Set `HOMEDASH_TRUSTED_PROXIES=*` to accept forwarded headers from any peer; only do that when the container port is reachable solely through your proxy, otherwise a client can spoof its address and sidestep the sign-in limit. `HOMEDASH_IMAGE` selects a published image instead of a local build. Run one Uvicorn worker per database; its background task owns the polling schedule.

## Add services and groups

1. Open **Groups** to create sections such as Infrastructure, Home, or Media.
2. Choose **Add service**. A preset can fill its name, description and icon; enter your own URL.
3. Select a group and icon. Under **Integration**, choose Link only, Website availability, Home Assistant, Proxmox VE, or another installed service definition. Configure its check directly in the same form.
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

### Audiobookshelf

Choose **Audiobookshelf** in the service editor and enter its HTTPS base URL and API key. The connector reads library totals, open sessions and recently synced sessions across users. The key must have permission to read `/api/users/online`, `/api/sessions` and its accessible libraries; a permission failure is shown as unavailable rather than zero listeners.

Expanded details show Sessions, Library items and Libraries, followed by up to three session rows with title, author, user (when reported), progress and remaining audio duration. **More sessions** reveals additional rows in document flow. Some clients sync playback without creating an open server session. The panel therefore merges open sessions with the most recent session per user updated within the last three minutes; each row is labelled Open session or Recently synced. Older history is not shown as a fallback. This is activity, not proof that audio is currently playing; paused/stopped syncs can remain visible until the recency window expires. Offline listening appears after syncing. Idle panels show “No open or recently synced sessions.”

Detail panels grow with their contents and push subsequent rows down, with no fixed height or internal scrollbar. Lists retain their disclosure state across refreshes and viewport changes. Failed checks retain both metrics and activity with a stale label and last successful data time. Remaining time is audio duration, without adjustment for playback speed.

Checks use the existing encrypted connection secrets and server polling; the browser never calls Audiobookshelf directly. No playback controls or changes to the Audiobookshelf server are made.

### AudioBookRequest

Choose **AudioBookRequest** under Integration and enter its HTTPS base URL and API key. The connector reads `/api/requests` and `/api/requests/manual` with bearer authentication. Counts cover visible book requests and manual requests: **Outstanding requests**, **Filled requests**, and **Errors**. Multiple requesters for the same catalog book count as one book request. Filled means marked downloaded in AudioBookRequest, including books supplied from other sources.

Outstanding rows show title, author, requester(s), and last request-update date, oldest first. Manual requests are labelled; rows with download errors are identified without exposing raw provider errors. Additional rows expand through **More requests**. Empty lists explicitly show **No outstanding requests**. Download errors produce Needs attention; API failures use the existing unavailable/stale behaviour. No requests or download settings are changed.

### Bambuddy

Choose **Bambuddy** under Integration and supply its HTTPS base URL and an API key with permission to read printer status. The connector uses `X-API-Key` and reads `/api/v1/printers/` and each printer’s `/status`. Each printer has its own section with **Connection**, **State**, **Nozzle / bed**, **AMS humidity**, and **Errors**. No print progress, archive statistics, camera or controls are shown.

Disconnected printers and reported printer errors show Needs attention. Disconnected devices do not present retained sensor values as current. Missing sensors use an explicit unavailable value; printers without AMS show Not fitted, and multiple AMS readings are labelled individually. API failures retain the previous sections with the standard stale label. Credentials and printer access codes are never returned to the browser.

### BookStack

Choose **BookStack** under Integration and enter the HTTPS base URL, Token ID and Token secret from the BookStack user profile. The secret uses HomeDash’s existing encrypted credential storage; blank retains it. Requests use BookStack’s `Authorization: Token ID:secret` header.

Details show **Books**, **Chapters**, and **Pages** totals plus the three **Recently updated pages**, with page title, book name and last update date. All results follow the token account’s content permissions. Counts use the API’s total fields rather than the number of returned rows. The connector reads list metadata and resolves book names as needed; it does not fetch page bodies, audit logs or edit content. Empty page lists and failed/stale refreshes use the normal explicit states.

### Immich

Choose **Immich** under Integration and enter its HTTPS base URL and an API key. The connector reads `/api/server/statistics`, `/api/server/storage` and `/api/jobs` with the `x-api-key` header, so the key needs only the **server.statistics**, **server.storage** and **job.read** permissions. A missing permission is shown as unavailable rather than as zero.

Details show **Photos**, **Videos**, **Library size** (all users) and **Disk used** (percentage of the volume Immich stores media on), with a **Background jobs** section below holding **Jobs pending** (active plus waiting, across all queues) and **Failed jobs**. A successful API response makes the service Online. Failed jobs, or a disk at least 90% full, show Needs attention; a queue backlog alone does not. No library contents, albums, people or thumbnails are read, and nothing in Immich is changed.

### Paperless-ngx

Choose **Paperless-ngx** under Integration, enter its HTTPS base URL and an API token (create it under your profile in Paperless). The connector reads `/api/statistics/` and `/api/status/` with `Authorization: Token`. A successful API response makes the service Online.

Details show **Documents**, **Inbox** (documents still waiting to be sorted) and **Disk used** (percentage of the volume Paperless stores documents on), with a **Health** section holding **Failed imports (30 days)** and **Components failing**. Failed imports, a disk at least 90% full, or a database, Redis, worker, search-index or sanity-check error show Needs attention; a full inbox alone does not. The classifier is not watched, because Paperless reports it as failing for a while after every import. System status is administrator-only in Paperless: with an ordinary user's token the card still shows the totals, without disk use or the Health section. Document titles and contents are never read, and nothing in Paperless is changed.

### Komga

Choose **Komga** under Integration, enter its HTTPS base URL and an API key (create it under your account settings in Komga). The connector reads `/api/v1/libraries`, `/api/v1/series` and `/api/v1/books` with the `X-API-Key` header, requesting one item per query so only the totals matter. A successful API response makes the service Online.

Details show **Series**, **Books**, **Unread** and **In progress**, with a **Library health** section holding **Libraries**, **Unavailable** (libraries Komga cannot reach), **Unreadable books** and **Unsupported books**. A library that is unavailable shows Needs attention. Unreadable and unsupported books are listed but do not change the status, because they stay until the file is replaced. Counts follow the key's user and its library access. No titles, covers or pages are shown, and nothing in Komga is changed.

### Calibre-Web

Choose **Calibre-Web** under Integration, enter its HTTPS base URL, a username and that user's password. Calibre-Web has no API tokens, so the connector signs in with the normal login form on every refresh and reads the total from the Books list table (`/ajax/listbooks`, one row requested). The password is stored encrypted like other secrets, so **use a dedicated user** with no admin, upload or edit rights; it needs only the **Show books list** permission. Without that permission, or with a wrong password, the card shows as unavailable instead of an empty library; HomeDash never displays provider error text.

Details show **Books**, the number of books in the library, and the **Last added book** (title, authors and the date it was added). Calibre-Web lists the newest book first and only that one row is requested. A successful sign-in and response make the service Online. Calibre-Web exposes no health, scan or disk information, so this card never shows Needs attention for the library itself. Calibre-Web limits sign-ins per user (3 a minute, 40 a day, reset on a successful sign-in), so a wrong saved password locks that user out for a while; fix the password in HomeDash and wait. Nothing else about your library is read, and nothing in Calibre-Web is changed. Single sign-on or reverse-proxy authentication in front of Calibre-Web is not supported by this connector.

### Gitea

Choose **Gitea** under Integration, enter its HTTPS base URL and an access token (Gitea: **Settings → Applications**). Select only **read:repository**, **read:issue** and **read:organization**. The connector sends `Authorization: token …` and reads `/api/v1/repos/search`, `/api/v1/repos/issues/search` and `/api/v1/orgs`, each asking for one item and using the `X-Total-Count` header, so no repository names, code or issue text is read. A successful API response makes the service Online. Forgejo uses the same API.

Details show **Repositories**, **Open issues**, **Open pull requests** and **Organizations**, as far as the token's user can see them; a token limited to public repositories counts only those. Gitea has no general health endpoint, so this card never shows Needs attention for the instance itself. **Do not give the token administrator (`read:admin`) access.** The connector does not need it, never calls an administrator endpoint, and that scope can read every user's email address and the CI runner registration token. Nothing in Gitea is changed.

### Mylar3

Choose **Mylar3** under Integration, enter its HTTPS base URL and the API key (Mylar3: **Configuration → Web Interface → API**; the API must be enabled). Mylar accepts the key only as a query parameter (`/api?apikey=…&cmd=…`), so it appears in the request URL and can end up in Mylar's or a reverse proxy's access logs; where you can, point HomeDash at an internal address. The connector calls `getIndex`, `getWanted` and `getUpcoming`. Mylar answers HTTP 200 even for errors, so a wrong key shows as unavailable, not as an empty library.

Details show **Series**, **Issues** (total issues tracked across all series), **Wanted** (issues Mylar is searching for) and **Upcoming**, with a **By status** section counting Active, Paused and Ended series. Mylar has no health endpoint, so this card never shows Needs attention for the instance itself. The download history and logs are not read: they grow without limit and are not needed. No titles are shown, and nothing in Mylar is changed.

### HomePod

HomePod is a self-hosted Docker management web app.

Choose **HomePod** under Integration, enter its HTTPS base URL and an API token. In HomePod, create the token under **Server settings → Access → API tokens** with the **Read** level; nothing more is needed or used. The connector reads `/api/hosts` and each host's `/containers` with bearer authentication.

Details show **Hosts online**, **Containers running** and **Not running**, followed by **Needs attention**, which is exactly what HomePod itself reports at `/api/attention`: unhealthy, restarting or dead containers, offline or unreachable hosts, agent certificates that are not renewing, and update-check problems. Pending image updates are listed as notices and do not change the card's status. Stopped containers are counted but not flagged, because many are one-shot jobs. Alerts ignored in HomePod do not appear; a note says how many are ignored. Any other item makes the card show Needs attention. A rejected or expired token is shown as unavailable rather than as an empty fleet. Nothing is started, stopped or changed.

### HomeCA

[HomeCA](https://github.com/macaon/homeca) is a small self-hosted certificate authority.

Choose **HomeCA** under Integration, enter its HTTPS base URL and an API token. In HomeCA, create the token under **Server settings → Access → API tokens** (the only level is **Read**). The connector reads one route, `/api/summary`, with bearer authentication. That token can never download a private key, list or fetch certificates, issue, revoke or change anything.

Details show **Certificates** (active leaves), **Expiring soon** (30 days or less), **Expired** and **Nearest CA expiry**, followed by **Needs attention**: an expired certificate or CA, a CA with fewer than 90 days left, a CA whose revocation list is overdue (HomeCA refreshes lists daily), a certificate you must renew by hand that is about to expire, and an ACME certificate with 7 days or less left (ACME clients renew at two-thirds of a certificate's life, so this means renewal is failing). An ACME certificate still inside its renewal window is not listed. The note says whether ACME is on. Names of certificates appear only in the attention list. A rejected or expired token is shown as unavailable rather than as an empty CA. Nothing is changed.

### Weather

Weather is a header widget rather than a service card. Use **+ Add widget**, choose **Weather**, and enter your latitude and longitude. It shows the current temperature and conditions above the dashboard; click it for feels-like, wind and a three-day forecast.

Data comes from [Open-Meteo](https://open-meteo.com/), which needs no API key. Your coordinates are sent to the API URL (default `https://api.open-meteo.com`), so point it at a self-hosted instance if you prefer. Units are °C and km/h.

### Public transport alerts

A header widget showing disruptions and planned works for buses and trains. Pick a **Country**; each country has its own data source and settings. Sweden and Norway are supported so far.

**Sweden** uses [Trafiklab](https://www.trafiklab.se/)'s GTFS Regional ServiceAlerts. Create a free Trafiklab project with **GTFS Regional Realtime** enabled and paste its API key as the token. Alerts are published per regional operator, and train events are often in a neighbouring region's feed. For Krösatåg and the Alvesta works in Kronoberg, choose Kronoberg, Kalmar and Jönköping (up to four regions). The optional **Only show** box narrows the list to the bus lines or words you care about, for example `12, 111, Alvesta, tåg`: numbers match affected lines, other terms match the alert text.

**Norway** uses [Entur](https://developer.entur.no/apis/open)'s national alert feed, which needs no key. Because it covers the whole country, **Only show** is required: enter places, lines or operator codes, for example `Oslo, R14, RUT`. Operator codes (RUT, VYG, ATB and so on) match the start of each alert's identifier. Entur asks every app to identify itself with a **Client name** in the form `company-application`, lowercase; the default `homelab_homedash` is fine for a home setup.

The feeds only contain what the operators publish as structured alerts, so this can be less than their own websites list. Each feed is fetched at most every ten minutes (Sweden) or five minutes (Norway), however short the refresh interval is, to stay inside Trafiklab's free key limit of 30,000 calls a month. The alert format is binary GTFS-Realtime, read by a small built-in decoder; no extra dependency is needed.

### Public transport times

A header widget for the next departures and arrivals at a stop, for all lines or only the ones you follow (for example `9, 14, 123`), with live delays where the operator provides them. Choose **+ Add → Widget → Public transport times**, pick a **Country**, enter any key it needs, then search for the stop by name. Sweden and Norway are supported so far.

**Sweden** uses [Trafiklab](https://www.trafiklab.se/)'s Realtime APIs: create a free project with **Trafiklab Realtime APIs** enabled and paste its key as the token. **Norway** uses [Entur](https://developer.entur.no/apis/open)'s Journey Planner and needs no key, only the **Client name** described above.

**Show in the header** sets what the chip and headline follow: **Departures**, **Arrivals**, or **Whichever is next**, which says for example "9 departs in 6 min" or "123 arrives in 4 min". The details always list both departures and arrivals, side by side, with the selected one first. Arrivals say where the vehicle comes from ("355 ← Braås centrum"). Widgets made before these settings existed keep showing Swedish departures.

Every row in the lists has a small icon for the kind of vehicle (bus, train, tram, metro or boat). **Vehicle types** limits the widget to the kinds you tick, for example only trains: a busy station can have a hundred buses an hour against a handful of trains, and train "line numbers" are train numbers that change daily, so a type filter is easier than a line filter. A rail replacement bus counts as a bus and is marked "Replacement bus": Entur reports its line as rail and only the submode says it is a bus. For Sweden this relies on the standard GTFS extended route type for replacement buses (714), which I have not yet seen in Trafiklab's data, so a Swedish replacement bus may still show as a train.

Live data is **not always available or accurate**, in any country. Many departures only have a timetable time and are shown as *Scheduled*, never as on time; the widget only says *On time*, *+5 min* or *Cancelled* when the operator supplies live data, and that data can still be late or wrong. Arrivals tend to have live data more often than departures, because those vehicles are already running. Treat it all as a guide. Only the next 60 minutes are available. The panel credits the data source (Trafiklab, CC BY 4.0; Entur, NLOD).

To stay inside the free limits, each list is fetched at most once every two minutes in Sweden (Trafiklab allows 25 calls a minute and 100,000 a month) or every minute in Norway (Entur's identified limit is far higher), however short the refresh interval is, and shared between widgets. The list the headline does not use is refreshed less often. HomeDash also stops polling this widget while nobody has the dashboard open, and backs off after an error such as an exceeded quota. A Swedish stop watched around the clock in **Whichever is next** mode uses about 43,000 calls a month.

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
- A field with `options` (a list of `{value, label}`) renders as checkboxes and is stored comma-separated.
- `providers` and `default_country`, for a connector that works differently per country: each provider (an object with `country_name`, `fields`, `secret_label`, `default_url`, `notice` and, for the times widget, `fetch()` and `search()` returning a normalised shape) is offered under a Country choice in the editor, and `provider(config)` picks it. Adding a country means adding a provider.
- Optional `notice` text, shown as a callout in the editor; `search_ui` plus an async `search(client, query, secret)` returning `[{label, values}]` to add a lookup box that fills form fields; and `on_demand = True` for metered APIs, which are then polled only while the dashboard is open.
- Optional `placement = 'header'` to show the connector as a header widget instead of on service cards, and `default_url` to prefill its API URL.
- Async `poll(client, base_url, config, secret)` returning a `Result(status, summary, metrics, details)`; `details` is optional.

Optional `details` adds an activity section with `heading`, `items`, and optional `empty`/`note` text. Rows support `title`, `subtitle`, `user`, `date`, optional numeric `progress` (0–100), and `remaining_minutes`. These are structured data, never HTML. The core escapes text and retains this section alongside stale metrics.

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

Additional outbound CA certificates are managed under **Server settings → Trust**. The same store supplies both service checks and OIDC; the OAuth / OIDC tab links there rather than duplicating certificate fields. Public system roots remain trusted.

Trust uses the shared `home-trust.js` and `home-trust.css` components, which are vendored in this repository. OIDC provider roots belong in Trust. App-specific scope details are under “Where this trust store applies.”

### Ping checks

In Add/Edit service, choose **Ping** as the service type and enter a hostname or IPv4/IPv6 address (without a URL or port). HomeDash sends three ICMP echo requests per check and shows average latency in milliseconds and packet loss. The service URL remains the link opened by the card. Checks run from the HomeDash server, not your browser. Partial loss shows Needs attention; no replies shows Offline. A host can block ICMP while its web service remains available.

The image includes `iputils-ping` and uses unprivileged ICMP sockets. The container network namespace must permit its group in `net.ipv4.ping_group_range` (the usual container default is `0 2147483647`). No raw-socket capability or privileged container is required.

Service cards keep their status dot visible and hide metrics by default. Use the chevron between the status dot and Open button on a checked card to open a full-width panel below its row, pushing later rows down. One panel is open at a time. The panel shows live details, including latency, packet loss, API metrics, errors and last check time. Expanded details continue updating with normal polling; link-only cards have no expander.

### Lucide icons

The service editor includes the complete bundled Lucide Static 1.64.0 catalog. The ten common icons appear first as large, unlabeled buttons, with names on hover. Expand the chevron to search names and keywords and browse the full icon-only catalog. A selected icon outside the common set is added to the visible shortcuts. Selection stays visible while searching; Show more icons browses the rest of the results. Existing service icons remain supported. Assets are served locally with no CDN requests.

`static/lucide/LICENSE` includes Lucide's license notices. To reproduce the pinned assets, run `python scripts/vendor_lucide.py` (requires network access to the npm registry). The importer verifies the package SHA-512 integrity and updates the SVG sprite and searchable catalog together.

### Reusing checks and moving cards

Choose **Integration**, then **Connection**. New services default to **New connection**; saved checks of the selected type are listed by name and target host. Choosing a saved check shares its polling/results and shows its settings read-only. Choose New connection for an independent configuration. Editing a service selects its current check automatically. **None** creates a link without monitoring.

Drag cards to reorder, or use **Move…** in a card's menu and select a position within its group using the keyboard. Header widgets work the same way: drag the chips above the dashboard, or open a widget and choose **Move…**. Widgets keep their own order, separate from the cards.

### Publishing images

Run `REGISTRY=registry.example.net ./build-push.sh` from this repository to publish `registry.example.net/homedash:latest` (or put the registry host name in a git-ignored `.registry` file and run `./build-push.sh`). Before replacing a different current image, the script preserves it under a UTC `YYYYMMDD-HHMMSS` tag derived from that image's own build metadata. New builds are published only as `latest`; timestamp tags are rollback images. Deployments normally use `:latest`, or an archived timestamp for rollback.

The Integration selector identifies a provider using its stable connector ID, independently of the card name or URL. Each integration supplies both availability and any live details: a successful authenticated API fetch is Online even when no playback sessions are open. A failed fetch changes the status and labels retained data stale. Website availability and Ping provide status for services without a dedicated integration.

## License

HomeDash is free software, released under the GNU General Public License, version 3. See [LICENSE](LICENSE).

The bundled icons are from [Lucide](https://lucide.dev), used under the ISC license; its notice is in [static/lucide/LICENSE](static/lucide/LICENSE).

## AI disclosure

Parts of this project's code, tests and documentation were written with the help of AI coding assistants. A human directs the work and reviews it. Use at your own risk.
