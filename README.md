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

### Manual model registration / モデルの手動登録

If a backend does not provide `GET /v1/models`, turn off model auto-discovery for that backend and register the model in **Admin → Model Management → Add model manually**. Specify the backend, the exact upstream model ID, and an optional public alias. A manual model remains routable without `/v1/models`. API capabilities are always selected manually using checkboxes.

## HTTPS certificates

`gateway.py` sets `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to `/etc/ssl/certs/ca-certificates.crt` on Linux. Certificate verification remains enabled.

Model removal: Administrators can delete manually added and discovered models. Deleted discovered IDs are excluded from future refreshes; manually adding the model again restores it. Editing the public model name does not change the upstream model ID.

## Admin dashboard (v0.3.6)

The admin interface has four tabs: **Backends**, **Models**, **API Keys**, and **Usage**. Changing tabs does not automatically refresh data or discard unsaved form inputs. Model discovery and its status are shown in the Models tab. Project attribution and the GitHub link appear in the footer.
