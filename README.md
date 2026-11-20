# OpenAI-Compat-Gateway

A self-hosted gateway for OpenAI-compatible APIs, Irodori-TTS-Server, and Kokoro-FastAPI. Register backends, publish models, and issue API keys from a web dashboard.

[日本語版 README](README.ja.md)

Developed by TAKAGO LAB, Kanazawa Institute of Technology. Not an official OpenAI product.

## API keys and endpoints

The gateway supports **Standard Keys** and **Extended Keys**.

| | Standard Key | Extended Key |
|---|---|---|
| Routing | By published `model` ID | To one selected backend |
| OpenAI-standard endpoints | Allowed models and endpoint permissions | Available if supported by the selected backend |
| **Extended endpoints** | **Not accessible** | **Accessible**, when the upstream supports them |
| Issued from | API Keys → Add → Standard Key | API Keys → Add → Extended Key |

**An Extended Key grants access to the selected backend's extended API endpoints, in addition to its supported standard endpoints.** For example, `GET /v1/audio/voices` is an extended endpoint provided by Irodori-TTS and Kokoro-FastAPI, rather than part of the standard speech-generation API.

```text
Same gateway URL (/v1/...)
 ├─ Standard Key → route by published model ID → OpenAI-standard APIs
 │                                          e.g. POST /v1/audio/speech
 └─ Extended Key → route to its assigned backend → extended APIs
                                               e.g. GET /v1/audio/voices
                                               e.g. POST /v1/audio/voices (Irodori)
                                               e.g. POST /v1/audio/voices/combine (supported Kokoro)
```

Each Extended Key is linked to exactly **one backend**. Registering two Irodori servers allows you to issue a separate Extended Key for each. Endpoint availability depends on the actual upstream server version.

## Installation

Requires Linux, Python 3, and systemd.

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

Open `http://127.0.0.1:8765/admin`, set an administrator password (at least 12 characters), and sign in. Use HTTPS for remote access. Example Caddy configuration:

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

The API base URL is then `https://ai.example.com/v1`.

## Initial setup

1. **Backends → Add:** register a backend ID, upstream URL (ending in `/v1`), and type (**OpenAI-compatible / Irodori-TTS / Kokoro-TTS**).
2. **Models:** discover or manually add models, then configure public model IDs and supported endpoints. Irodori and Kokoro models remain visible in this tab.
3. **API Keys → Add:** for a Standard Key, choose allowed models and functions. For an Extended Key, choose one registered backend.
4. **Copy the key on issuance:** only a hash is saved, so the full secret cannot be displayed later.

Lime-colored extended endpoint badges are **potential capabilities inferred from the backend type**, not live confirmation. Check which APIs your installed upstream actually implements.

If the upstream requires authentication, configure its credential in backend settings. Upstream credentials are separate from client-facing gateway API keys.

### Managing keys

The **API Keys** tab manages names, expiration dates, enabled status, permissions, rotation, and deletion. The *key name* is an administrative label, *key ID* is an internal numeric identifier, and *API key* is the secret issued to clients.

The **Usage** tab shows request counts and response-start latency for both Standard and Extended Keys.

## API examples

The URLs and environment-variable names below are examples. Replace them with your gateway URL and actual keys.

### Standard Key: speech generation

For a published Irodori model named `irodori-tts`, standard OpenAI speech generation works with a Standard Key, provided the model and endpoint are allowed:

```bash
curl https://ai.example.com/v1/audio/speech \
  -H "Authorization: Bearer $STANDARD_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"irodori-tts","input":"こんにちは","voice":"hadou","response_format":"wav"}' \
  --output speech.wav
```

Standard Keys can also call configured chat, image, and other OpenAI-standard endpoints.

### Extended Key: Irodori voice listing and management

An Extended Key issued for an Irodori-TTS backend accesses that server's extended endpoints:

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $IRODORI_KEY"
```

Voice uploads, reads, updates, and deletions follow the Irodori-TTS-Server API. The same Extended Key can also call `POST /v1/audio/speech`.

### Extended Key: Kokoro voice listing

An Extended Key for a Kokoro-TTS backend can retrieve its voices:

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $KOKORO_KEY"
```

If supported by the installed Kokoro-FastAPI, extended endpoints such as `POST /v1/audio/voices/combine` are also forwarded.

## Upgrade and operations

Back up `/var/lib/openai-compat-gateway/gateway.sqlite3` before upgrading. Existing keys and configuration are preserved through the schema migration.

```bash
git pull
sudo ./install.sh
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
```

### Security notes

- **An Extended Key grants broad access to the assigned backend's `/v1/` API**, including mutating operations such as voice deletion. Give these keys only to trusted clients.
- HTTP APIs under `/v1/` are supported; WebSockets are not.
- Request bodies are limited to 256 MiB by default; responses support streaming.
- Protect the admin dashboard with HTTPS and access restrictions. Upstream credentials are stored in SQLite without encryption at rest.
- Automated tests use mock upstreams. Validate real Irodori/Kokoro interoperability separately.

## Acknowledgements

Thanks to **Aratako**, developer of [Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server), and **remsky and contributors**, developers of [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI).

## License

MIT License. Copyright (c) 2026 TAKAGO LAB.
