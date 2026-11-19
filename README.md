# OpenAI-Compat-Gateway

A lightweight, self-hosted gateway that unifies OpenAI-compatible API servers across multiple PCs behind a single `/v1` endpoint. Developed by **TAKAGO LAB**, Kanazawa Institute of Technology. Not affiliated with OpenAI.

## One-command installation

For Linux with systemd, Python 3/venv/pip and Git:

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

This creates a non-login system user `openai-gateway`, installs the application in `/opt/openai-compat-gateway`, and registers a **system-level** systemd service that runs as that user. No `.env`, `config.json`, initial API key, manual administrator password hash, or session secret is required.

Open `/admin` (over HTTPS via Caddy, or `http://127.0.0.1:8765/admin` locally), set a 12+ character administrator password, and sign in. Add upstream `/v1` endpoints via the dashboard, discover and publish models, then issue client API keys with service/model permissions and expiration dates. Backend API keys may be entered in the dashboard.

**First-run security warning:** The first visitor to `/admin` can register the administrator without source-IP restrictions. Complete setup immediately and do not expose the uninitialized service publicly. Use HTTPS for remote administration. Backend secrets are saved in owner-restricted SQLite but are **not encrypted at rest**.

## Caddy

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

API base URL: `https://ai.example.com/v1`; management page: `https://ai.example.com/admin`.

## Operations

```bash
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
sudo systemctl restart openai-compat-gateway
```

To update, `git pull` in your local clone and rerun `sudo ./install.sh`. Persistent data is stored in `/var/lib/openai-compat-gateway/gateway.sqlite3` and is preserved by reinstallations. Back it up before upgrades.

Features include unified `/v1/models`, per-key ACL and expiration, SSE streaming, passthrough JSON `extra_body`, multipart upload forwarding, and persistent asynchronous video job mapping. Newly discovered models remain unpublished until an administrator enables them.

MIT License. Copyright (c) 2026 TAKAGO LAB.

## Administration and model-centric routing (v0.4.0)

The dashboard has four tabs: Backends, Models, API Keys, Usage.

- **Backends:** Register a `/v1` URL and optional upstream API key. Capabilities are *derived* from the endpoints configured on models attached to that backend. Model discovery via `GET /v1/models` is optional. A missing `/v1/models` endpoint does not prevent manual model registration.
- **Models:** Auto-discover or manually add a model; configure its public alias, published state, and supported endpoints as explicit `POST /v1/...` selections. Newly discovered models default to unpublished with no endpoints. Hide/show only affects the admin list. Unpublishing blocks API use and removes it from `/v1/models`. Deletion removes the registration. Refreshing the model list restores deleted discovered models that remain on the upstream server, initially unpublished and with no enabled endpoints. Manually added models must be re-added manually.
- **API Keys:** Generate time-limited tokens, constrain exact endpoints and models, revoke, rotate or delete. Secrets are only shown when issued/rotated.
- **Usage:** View request statistics and export/restore the SQLite configuration. Restore requires the current administrator password and service restart. **Backups contain upstream secrets; handle them as sensitive.**

Model endpoint permissions are applied at routing time, including for SSE streaming and multipart image/audio requests. Video job read/delete is protected by the video endpoint permission and the owning API key. Legacy stored capabilities are migrated in-place to endpoint identifiers; back up the SQLite database before updating.

To create a consistent backup before installing a version with database schema changes:

```bash
sudo systemctl stop openai-compat-gateway
sudo install -m 600 /var/lib/openai-compat-gateway/gateway.sqlite3 \
  /var/lib/openai-compat-gateway/gateway.sqlite3.pre-v0.4.0
sudo systemctl start openai-compat-gateway
```

## HTTPS certificate trust

`gateway.py` configures `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to `/etc/ssl/certs/ca-certificates.crt` as requested for the deployment environment. HTTPS certificate verification stays enabled; this does not automatically fix an incomplete certificate chain.

MIT License. Copyright (c) 2026 TAKAGO LAB.

## v0.4.4: Model endpoint editor

The model table shows only configured endpoints as compact labels such as `v1/chat/completions`. Click **Edit** on an individual model to choose its endpoints with checkboxes; **Save** applies the change and **Cancel** discards it.

Newly discovered models begin with **no endpoint assignments**. The gateway does not infer model capabilities. Existing model endpoint assignments are preserved across upgrades. Bulk editing of public model IDs and publication state remains available.

### Deleting and rediscovering models

Deleting an automatically discovered model removes only its gateway registration. Refreshing `/v1/models` restores it if the upstream still lists it, initially unpublished, visible, and with no enabled endpoints. Manually added models are not automatically restored. Existing model settings remain unchanged. The Edit action sits in the endpoint column.

## Administration UI (v0.5.0)

The four tabs are Backends, Models, API Keys, and Usage. Lists are read-only summaries; use **Edit** on an individual item for a single Save/Cancel form. Model editing combines public model ID, visibility to API clients (publication), and endpoint permissions. Hide affects only the admin list, while Delete removes the gateway registration. Models can be discovered again if their upstream `/v1/models` reports them. Model search and backend filtering are available. Existing API and database formats remain unchanged.

## Compact administration UI (v0.5.1)

The admin interface uses compact tables on desktop for backends, models, and API keys. Search, filters, and actions share one toolbar. Select **Edit** to expand a row-level form. On narrow screens, tables can be scrolled horizontally. Existing API routes and database records are unchanged.
