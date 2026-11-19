# OpenAI-Compat-Gateway


A lightweight, unofficial gateway that consolidates OpenAI-compatible AI APIs running on multiple PCs behind **one `/v1` endpoint**. Supports multiple Ollama/vLLM servers, TTS, STT, image generation/editing, and asynchronous video jobs.

**v0.2.2** adds an administrator web UI, SQLite-backed backend and model registry, automatic `/v1/models` discovery, model publishing and aliases, API keys with capability/model ACLs, persistent video ownership, and request statistics.

## Quick start

Requires Python 3.10+.

```bash
unzip OpenAI-Compat-Gateway-v0.2.2.zip
cd OpenAI-Compat-Gateway
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
cp .env.example .env
chmod 600 .env
.venv/bin/python setup_admin.py
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Edit `config.json` to list your actual upstream URLs and capabilities. This file seeds the SQLite registry at first startup; subsequent backend/model changes are managed in the admin UI. Every upstream base URL must end with `/v1`. Set the generated `ADMIN_PASSWORD_HASH` and `ADMIN_SESSION_SECRET` in `.env` and change `GATEWAY_API_KEY` to a sufficiently strong initial API key. Then:

```bash
set -a; source .env; set +a
.venv/bin/uvicorn gateway:app --host 127.0.0.1 --port 8765
```

The admin dashboard is at `/admin`. **Use HTTPS through Caddy** for admin login, since cookies use the `Secure` flag by default. For local HTTP-only testing you may temporarily set `ADMIN_COOKIE_SECURE=false`. See `Caddyfile.example` for an internal-network restriction on `/admin`.

## Admin workflow

1. Add or modify upstream servers with their URL, capabilities, and optional API key environment-variable name.
2. Fetch their `/v1/models` list. Newly detected models start **unpublished**; existing explicitly configured models are imported as published once during bootstrap.
3. Publish selected models, choosing unique public IDs when needed.
4. Create user API keys with permitted capabilities (`chat`, `audio_speech`, `videos`, etc.) and allowed public model IDs.
5. Distribute the one-time displayed key; it cannot be recovered afterward. API keys can be disabled or have permissions edited without restarting.

The gateway checks ACLs when listing models **and** when forwarding actual requests. Video job ownership is saved in SQLite, so a different API key cannot read or cancel another key's jobs.

## API and routes

`GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/completions`, `POST /v1/responses`, `POST /v1/embeddings`, `POST /v1/audio/transcriptions`, `POST /v1/audio/translations`, `POST /v1/audio/speech`, `POST /v1/images/generations`, `POST /v1/images/edits`, `POST /v1/images/variations`, and video creation/polling/content/remix/deletion endpoints at `/v1/videos*`.

The model's public ID is mapped to the upstream model ID when relaying JSON or multipart requests. Requests missing a model are rejected if more than one published eligible model is available. No GPU load balancing is performed.

## OpenAI SDK `extra_body` and provider-specific parameters

The gateway transparently forwards provider-specific JSON fields. The OpenAI Python SDK merges `extra_body` into the top-level JSON payload; the gateway preserves all fields except that it rewrites `model` when the public ID differs from the upstream ID. Custom multipart form fields for audio and image endpoints are preserved too. The upstream must recognize each parameter.

```python
from openai import OpenAI
client = OpenAI(base_url="https://ai.example.com/v1", api_key="YOUR_GATEWAY_KEY")
result = client.chat.completions.create(
    model="qwen-local", messages=[{"role":"user","content":"hello"}],
    extra_body={"think": False, "options": {"num_ctx": 32768}},
)
```

## systemd and Caddy

The included `openai-compat-gateway.service` is a user service expecting the project at `~/OpenAI-Compat-Gateway`, with `.env` and `.venv` configured:

```bash
mkdir -p ~/.config/systemd/user
cp openai-compat-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now openai-compat-gateway.service
journalctl --user -u openai-compat-gateway.service -f
```

To allow it to remain active when logged out: `sudo loginctl enable-linger "$USER"`.

See `Caddyfile.example` for an example that forwards HTTPS to `127.0.0.1:8765`, while restricting `/admin` to trusted networks. Firewall rules on backend machines still need to allow the gateway host to reach the AI services; backend ports do not need to be publicly exposed.

## Security and limitations

- Store `.env` and `gateway.sqlite3` securely. The database contains API key hashes, backend URLs, user access rules, and usage metadata.
- Admin sessions are authenticated independently from user API keys, use PBKDF2-SHA256 password verification, signed cookies and CSRF checks, and are revocable at logout.
- Backend URLs are administrator-controlled and can access internal networks; do **not** expose `/admin` publicly without network access controls.
- Upload payloads are currently buffered in memory, subject to `max_request_mb`. This is not optimized for very large video uploads.
- Timing metrics measure time to initial upstream HTTP response, not full streaming generation duration.
- Model capability types are set by the administrator on each backend; `/v1/models` does not reliably convey capabilities.
- There is no automatic GPU balancing, rate limiting, or detailed GPU monitoring. Upstream OpenAI video API compatibility varies by backend.

Run tests: `pip install -r requirements-dev.txt && python -m pytest -q`.

Copyright (c) 2026 TAKAGO LAB. MIT licensed. Not affiliated with OpenAI.

## API key expiration

In the Admin UI, choose **Never**, **7 days**, **30 days**, **90 days**, or **custom date/time (JST)** when issuing a key. The key table supports editing, extending, or clearing the expiry (blank = never). Relative durations start at issuance.

The SQLite `api_keys.expires_at` column stores a UTC Unix timestamp in seconds. After expiry, all `/v1/` requests return `401 Unauthorized`, including model listing, streaming, and video job operations. Expired keys remain visible for administrative renewal. Existing databases are upgraded by adding the column; existing keys remain non-expiring.
