"""Conservative, non-destructive discovery of OpenAI-compatible API routes.

Only metadata and safe GET/OPTIONS are used; inference POST is never sent.
A missing/ambiguous response is *not* evidence that a previously configured
capability has disappeared.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx

ENDPOINTS = {
    "chat": "chat/completions",
    "completions": "completions",
    "responses": "responses",
    "embeddings": "embeddings",
    "audio_transcriptions": "audio/transcriptions",
    "audio_translations": "audio/translations",
    "audio_speech": "audio/speech",
    "images_generations": "images/generations",
    "images_edits": "images/edits",
    "images_variations": "images/variations",
    "videos": "videos",
}


def capabilities_from_openapi(document: object, base_path: str) -> set[str]:
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise ValueError("OpenAPI paths missing")
    paths = document["paths"]
    prefix = base_path.rstrip("/")
    detected = set()
    for capability, suffix in ENDPOINTS.items():
        candidates = {f"{prefix}/{suffix}", f"/v1/{suffix}"}
        if prefix == "/v1":
            candidates.add(f"/{suffix}")
        if any(isinstance(paths.get(p), dict) and
               any(method.lower() == "post" for method in paths[p]) for p in candidates):
            detected.add(capability)
    return detected


def _allows_post(response: httpx.Response) -> bool:
    return "POST" in {item.strip().upper() for item in response.headers.get("allow", "").split(",")}


async def detect_capabilities(client: httpx.AsyncClient, base_url: str, headers: dict) -> dict:
    """Find *positively identified* routes without making any generation calls.

    POST in a valid target's Allow header is evidence only if a deliberately
    nonexistent sibling route does not advertise the same thing. Authentication
    failures and generic fallback handlers remain inconclusive.
    """
    base_url = base_url.rstrip("/")
    path = urlsplit(base_url).path.rstrip("/")
    root = base_url[:-3] if path.endswith("/v1") else base_url
    openapi_reason = ""
    try:
        response = await client.get(root + "/openapi.json", headers=headers, timeout=5)
        if response.status_code == 200:
            found = capabilities_from_openapi(response.json(), path)
            if found:
                return {"status": "ok", "source": "openapi", "capabilities": sorted(found),
                        "message": "OpenAPIのPOSTルートを確認しました．"}
            openapi_reason = "OpenAPIに対応POSTルートの記載がありません"
        else:
            openapi_reason = f"OpenAPI: HTTP {response.status_code}"
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        openapi_reason = f"OpenAPI: {str(exc)[:100]}"

    # A single control route is sufficient to detect global middleware that
    # returns generic 405 or OPTIONS Allow: POST for every URL.
    control = base_url + "/__openai_compat_gateway_missing_route_92c4b8"
    try:
        control_get, control_options = await asyncio.gather(
            client.get(control, headers=headers, timeout=5),
            client.options(control, headers=headers, timeout=5),
        )
    except httpx.HTTPError:
        control_get = control_options = None

    async def probe(capability: str, suffix: str):
        target = base_url + "/" + suffix
        for method in ("GET", "OPTIONS"):
            try:
                response = await client.request(method, target, headers=headers, timeout=5)
            except httpx.HTTPError:
                continue
            baseline = control_get if method == "GET" else control_options
            if baseline is None or not _allows_post(response):
                continue
            # 405 on GET with Allow POST or an explicit OPTIONS Allow POST is
            # meaningful only if the nonexistent route is distinguishable.
            if method == "GET" and response.status_code != 405:
                continue
            if method == "OPTIONS" and response.status_code not in (200, 204, 405):
                continue
            if _allows_post(baseline):
                continue
            if baseline.status_code in (401, 403):
                continue
            return capability
        return None

    results = await asyncio.gather(*(probe(name, suffix) for name, suffix in ENDPOINTS.items()))
    found = sorted(x for x in results if x)
    if found:
        return {"status": "partial", "source": "http-probe", "capabilities": found,
                "message": "HTTPで存在を確認できた機能です．未検出は非対応を意味しません．"}
    return {"status": "unknown", "source": "none", "capabilities": [],
            "message": "機能を確定できませんでした．既存設定は保持します． " + openapi_reason[:120]}
