"""Supported model endpoints, with stable legacy-to-endpoint migration."""
ROUTES = {
    "chat": "POST /v1/chat/completions", "completions": "POST /v1/completions",
    "responses": "POST /v1/responses", "embeddings": "POST /v1/embeddings",
    "audio_transcriptions": "POST /v1/audio/transcriptions",
    "audio_translations": "POST /v1/audio/translations", "audio_speech": "POST /v1/audio/speech",
    "images_generations": "POST /v1/images/generations",
    "images_edits": "POST /v1/images/edits", "images_variations": "POST /v1/images/variations",
    "videos": "POST /v1/videos",
}
ENDPOINTS = set(ROUTES.values())
def normalize(values):
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError("Expected a list of endpoint strings")
    result = [ROUTES.get(v, v) for v in values]
    if not set(result) <= ENDPOINTS | {"*"}:
        raise ValueError("Unsupported endpoint")
    return sorted(set(result))
