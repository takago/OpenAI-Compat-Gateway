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

## Administration improvements (v0.3.1)

- Select backend capabilities using checkboxes. Saving a backend immediately tries OpenAI-compatible `/v1/models` discovery and reports successes or detailed errors.
- Refresh every backend explicitly; publish discovered models using checkboxes and batch save. Discovered models remain private by default.
- Copy an API key at issue or rotation time. Previously issued secrets cannot be displayed again because only their hashes are stored. Rotation revokes the old secret; keys can be deleted or their permissions and expiration modified.
- Existing SQLite data is preserved. Back up the database before installing an update with `sudo ./install.sh`.

## Automatic API capability discovery (v0.3.2)

On backend save and explicit refresh, the gateway collects models from `/v1/models` and independently discovers endpoint capabilities. It checks `/openapi.json` for POST operations first, then uses **GET/OPTIONS only** (with a nonexistent-path control) when OpenAPI metadata is missing. It never submits generation POST requests during discovery.

New backends default to automatic discovery. If endpoints cannot be verified, existing capability assignments are retained and the dashboard reports an unknown result; administrators can switch to manual selection. Existing backends retain their previous manual configuration after upgrade until explicitly switched to automatic discovery. Detection proves route existence, not successful model execution. Models remain unpublished by default.
