# OpenAI-Compat-Gateway

A lightweight, self-hosted gateway for accessing OpenAI-compatible API servers running on multiple PCs through a single `/v1` endpoint. Issue client API keys and control which models and endpoints each key can use.

Developed by TAKAGO LAB, Kanazawa Institute of Technology. Not affiliated with OpenAI.

[日本語README](README.ja.md)

## Features

- Combine multiple upstream servers under one API base URL
- Set public model aliases, publication state, and supported endpoints per model
- Issue client API keys with model/endpoint permissions and expiration dates
- Forward OpenAI-compatible chat, Responses, embeddings, images, audio, and video APIs
- Forward SSE streams, multipart uploads, and extra JSON parameters
- Support Irodori-TTS-Server speech generation and voice management
- Configure and monitor the gateway, and back up or restore configuration, in a web dashboard

## Installation

Requires Linux with Python 3, venv, pip, Git, and systemd. Install `python3-venv` if needed.

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

The installer creates a dedicated non-login system user, `openai-gateway`, deploys the application to `/opt/openai-compat-gateway`, and starts a system-level systemd service. You do not need to prepare `.env`, `config.json`, or an initial API key.

## First-time setup

1. On the gateway host, open `http://127.0.0.1:8765/admin`.
2. Create an administrator password (at least 12 characters) and sign in.
3. Under **Backends**, add each upstream server's `/v1` URL and optional API key.
4. Under **Models**, discover models or add them manually. Use **Edit** to configure the public model ID, publication status, and supported endpoints.
5. Under **API Keys**, issue a client key and assign allowed models, endpoints, and expiration.

**Security:** The first visitor to `/admin` can register the administrator. Finish initial setup before exposing the service publicly. Use HTTPS for remote administration.

### Dashboard behavior

- Newly discovered models are **unpublished and have no enabled endpoints** until configured.
- **Hide** affects only the admin list; it does not unpublish a model.
- **Delete** removes a model registration. Discovered models can be restored by refreshing the upstream model list (initially unpublished with no endpoints). Manually added models must be manually re-added.
- Backends without `/v1/models` can still use manually registered models.
- Client API key secrets are shown only at issuance or rotation.

## Connecting clients

Example Caddy reverse proxy:

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

- API base URL: `https://ai.example.com/v1`
- Dashboard: `https://ai.example.com/admin`

List published models:

```bash
curl -H "Authorization: Bearer $API_KEY" \
  https://ai.example.com/v1/models
```

For API requests, provide the configured **public model ID** in `model`. The gateway selects the appropriate backend.

## Irodori-TTS-Server integration

To use [Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server):

1. Under **Backends**, add the Irodori `/v1` URL (for example, `http://192.168.1.20:8088/v1`).
2. Under **Models**, discover or manually add upstream model `irodori-tts`, and choose a public alias (for example, `irodori-public`).
3. Publish that model and enable **`POST /v1/audio/speech`**.
4. Grant your client key permission to use the model and the speech endpoint.

Example speech request:

```bash
curl -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"irodori-public","input":"Hello","voice":"none","response_format":"wav","irodori":{"caption":"A calm voice"}}' \
  https://ai.example.com/v1/audio/speech --output speech.wav
```

The gateway forwards `voice`, `speed`, `response_format`, `stream_format`, and Irodori-specific options without interpreting them. Audio bytes and SSE streaming are supported. File paths such as `irodori.ref_wav` refer to files on the **upstream Irodori server**.

### Voice listing and management

The gateway also forwards these Irodori-specific endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/v1/audio/voices` | List voices |
| POST | `/v1/audio/voices` | Upload a voice |
| GET | `/v1/audio/voices/{voice_id}` | Get voice metadata |
| PUT | `/v1/audio/voices/{voice_id}` | Replace a voice |
| DELETE | `/v1/audio/voices/{voice_id}` | Delete a voice |

When more than one speech backend is eligible, select the public model with `?model=...`. Omit the selector if only one backend is eligible.

```bash
curl -H "Authorization: Bearer $API_KEY" \
  'https://ai.example.com/v1/audio/voices?model=irodori-public'
```

**Permissions:** All voice-management operations, including uploads and deletion, use the same `audio_speech` permission as speech generation. Be careful when issuing client keys. `GET /v1/audio/voices` is an Irodori extension, not a standard OpenAI endpoint.

## Operations and updates

```bash
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
sudo systemctl restart openai-compat-gateway
```

To update, run `git pull` in your clone and rerun `sudo ./install.sh`. Persistent configuration lives at `/var/lib/openai-compat-gateway/gateway.sqlite3`; reinstalling preserves it. Back it up before updating.

The **Usage** tab provides configuration backup and restore. Restoring requires the administrator password and a service restart. Backups contain upstream credentials and must be protected.

### Security notes

- Use HTTPS for remote administration.
- Upstream secrets are stored in access-restricted SQLite but are **not encrypted at rest**.
- HTTPS certificate verification remains enabled; the gateway refers to `/etc/ssl/certs/ca-certificates.crt` for CA certificates.
- Check per-model endpoints and client key permissions before publishing models.

## License

MIT License. Copyright (c) 2026 TAKAGO LAB.
