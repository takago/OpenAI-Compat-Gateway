# OpenAI-Compat-Gateway

A self-hosted gateway for **OpenAI-compatible APIs** and dedicated **Irodori-TTS-Server / Kokoro-FastAPI** backends. One gateway URL, with two simple routing rules: normal API keys select an OpenAI-compatible model; a dedicated TTS key selects exactly one backend.

[日本語版 README](README.ja.md)

Developed by TAKAGO LAB, Kanazawa Institute of Technology. Not affiliated with OpenAI.

## Features

- **OpenAI-compatible:** existing model aliases, endpoint permissions, model discovery, regular API keys, and chat / images / audio / video forwarding remain supported.
- **Irodori-TTS / Kokoro-TTS:** create a dedicated API key from the **API Keys** tab for the selected backend. The key selects the backend; the gateway forwards the original `/v1/...` method, path, query, JSON or multipart body, and streaming response without rewriting `model` or `voice`.
- The admin UI manages backend registration and regular API keys. Configuration and key hashes live in SQLite.
- A dedicated key never authorizes another backend. A regular key never authorizes a dedicated backend.

| Backend type in the UI | Key management | Routing |
|---|---|---|
| OpenAI-compatible | Regular API key (model / endpoint permissions) | Request `model` |
| Irodori-TTS | Dedicated key issued from API Keys tab | API key |
| Kokoro-TTS | Dedicated key issued from API Keys tab | API key |

## Install and open the dashboard

Linux, Python 3, and systemd are required.

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

Open `http://127.0.0.1:8765/admin`, create an administrator password (12+ characters), and sign in. Complete first-time setup **before** exposing the gateway to the network. Use HTTPS for remote access.

A possible reverse proxy configuration:

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Your API base URL is then `https://ai.example.com/v1`; the dashboard is at `/admin`.

## Register a backend

In **Backends → Add**, enter a backend ID, its API URL ending in `/v1`, and select a type from the dropdown:

- **OpenAI-compatible:** keep the existing model discovery / manual registration workflow. Publish models and choose supported endpoints under **Models**. Create regular keys under **API Keys** and assign model / endpoint permissions.
- **Irodori-TTS:** register the Irodori-TTS-Server `/v1` URL (for example `http://192.168.1.20:8088/v1`).
- **Kokoro-TTS:** register the Kokoro-FastAPI `/v1` URL (for example `http://192.168.1.21:8880/v1`).

If the upstream requires authentication, configure its key in the backend settings. This is separate from the key issued to gateway clients.

## API key issuance and management

Open the **API Keys** tab and click **Issue** (Add):

- **OpenAI-compatible:** choose the allowed endpoints and models, as before.
- **Dedicated API:** select exactly one registered Irodori-TTS or Kokoro-TTS backend. Multiple backends can each have their own client key.

Key expiration, enabled status, rotation, and deletion are all managed under **API Keys**. Only the key hash is stored, so **copy the full secret when issued or rotated**; it cannot be displayed again later.

Backend registration or type changes do not automatically issue keys. Switching between Irodori-TTS and Kokoro-TTS retains any existing dedicated keys. Switching to OpenAI-compatible revokes and deletes the keys attached to that backend. Previously registered OpenAI model records are preserved for later use.

## Use the API

### OpenAI-compatible (regular key)

Use the published model ID, as before:

```bash
curl https://ai.example.com/v1/chat/completions \
  -H "Authorization: Bearer $REGULAR_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"my-chat-model","messages":[{"role":"user","content":"Hello"}]}'
```

OpenAI-compatible speech generation continues to use the `model` field and a **regular** key. Voice-listing / voice-management endpoints are *not* part of the standard OpenAI-compatible profile.

### Irodori-TTS (dedicated key)

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $IRODORI_KEY"

curl https://ai.example.com/v1/audio/speech \
  -H "Authorization: Bearer $IRODORI_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"irodori-tts","input":"こんにちは","voice":"hadou","response_format":"wav"}' \
  --output speech.wav
```

Voice uploads and management use the Irodori-TTS-Server API (`POST /v1/audio/voices`, `GET/PUT/DELETE /v1/audio/voices/{voice_id}`, etc.). `model`, `voice`, and Irodori-specific fields are forwarded unchanged.

### Kokoro-TTS (dedicated key)

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $KOKORO_KEY"

curl https://ai.example.com/v1/audio/speech \
  -H "Authorization: Bearer $KOKORO_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","input":"Hello","voice":"af_bella","response_format":"mp3"}' \
  --output speech.mp3
```

Other Kokoro-FastAPI endpoints under `/v1/`, including `/v1/audio/voices/combine` where offered, are passed to the selected backend. Supported options are determined by the installed upstream server version. Unsupported paths return the upstream response.

**Multiple servers:** you can issue distinct keys for two Irodori registrations; `/v1/audio/voices` and `/v1/audio/speech` are sent to the backend selected by the key. `voice` remains a regular upstream request field.

## Upgrade and operations

Re-running `sudo ./install.sh` updates the service and keeps the persistent SQLite database at `/var/lib/openai-compat-gateway/gateway.sqlite3`. The schema migration retains existing regular keys and migrates previously issued dedicated-key hashes into the API Keys list without changing the secrets. Previously registered backends default to **OpenAI-compatible**.

```bash
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
sudo systemctl restart openai-compat-gateway
```

The dashboard also offers SQLite backup / restore. Backups contain upstream credentials and key hashes: protect them. As before, upstream credentials are stored in an access-restricted database **without encryption at rest**.

### Security and limitations

- Only registered, enabled backends are reachable with their dedicated keys, via paths under `/v1/`. The gateway does not proxy non-`/v1/` endpoints or WebSockets.
- Dedicated forwarding deliberately passes through upstream `GET`, `POST`, `PUT`, and `DELETE` calls, including voice changes. Treat each dedicated key as granting the corresponding upstream `/v1/` permissions. Restrict network access and use HTTPS.
- This implementation uses buffered request bodies (256 MiB default limit) and streamed upstream responses. It does not transparently support every HTTP protocol feature.
- Test each actual upstream version separately. Local automated tests use mock servers rather than real Irodori/Kokoro inference.

## Acknowledgements

Thanks to **Aratako** for developing [Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server), and to **remsky and contributors** for developing [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI). These are independent open-source projects; this gateway is not affiliated with their developers.

## License

MIT License. Copyright (c) 2026 TAKAGO LAB.
