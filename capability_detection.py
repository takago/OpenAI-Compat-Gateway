"""Non-destructive discovery of OpenAI-compatible HTTP endpoints.

An endpoint can exist without being operational. Never issue inference POSTs.
"""
from __future__ import annotations

import asyncio
import json
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
    """Only count POST routes that match the base URL's /v1 prefix."""
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise ValueError("OpenAPI paths missing")
    result = set()
    prefix = base_path.rstrip("/")
    for cap, path in ENDPOINTS.items():
        candidates = {prefix + "/" + path, "/v1/" + path}
        if prefix == "/v1":
            candidates.add("/" + path)
        if any(isinstance(document["paths"].get(p), dict) and "post" in document["paths"][p]
               for p in candidates):
            result.add(cap)
    return result


async def detect_capabilities(client: httpx.AsyncClient, base_url: str, headers: dict) -> dict:
    """Use OpenAPI first. If unavailable, probe GET/OPTIONS without generating content.

    A 405 is usable only when a nonexistent control path does NOT behave alike.
    OPTIONS is trusted only when Allow explicitly includes POST. Ambiguous routes
    remain unverified, not silently marked as present.
    """
    root = base_url[:-3] if base_url.endswith("/v1") else base_url
    parsed = urlsplit(base_url)
    problems = []
    try:
        r = await client.get(root + "/openapi.json", headers=headers, timeout=5)
        if r.status_code == 200:
            found = capabilities_from_openapi(r.json(), parsed.path)
            return {"status": "ok", "source": "openapi", "capabilities": sorted(found), "message": ""}
        problems.append(f"OpenAPI: HTTP {r.status_code}")
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"OpenAPI: {str(exc)[:100]}")

    # Some servers expose a global catch-all 405/OPTIONS handler. Compare it
    # to a deliberately nonexistent sibling endpoint before believing a probe.
    async def probe(cap: str, path: str):
        target = base_url + "/" + path
        control = base_url + "/__ocg_probe_nonexistent_728e0d/" + path
        try:
            a, b = await asyncio.gather(
                client.get(target, headers=headers, timeout=4),
                client.get(control, headers=headers, timeout=4),
            )
            if b.status_code == a.status_code and b.headers.get("allow", "") == a.headers.get("allow", ""):
                return None
            if a.status_code == 405 and b.status_code in (404, 405):
                allow = {m.strip().upper() for m in a.headers.get("allow", "").split(",")}
                if not allow or "POST" in allow:
                    return cap
            # A GET response alone does not prove that POST is supported.
            opt = await client.options(target, headers=headers, timeout=4)
            ctrl = await client.options(control, headers=headers, timeout=4)
            allow = {m.strip().upper() for m in opt.headers.get("allow", "").split(",")}
            ctrl_allow = {m.strip().upper() for m in ctrl.headers.get("allow", "").split(",")}
            if "POST" in allow and "POST" not in ctrl_allow:
                return cap
        except httpx.HTTPError:
            pass
        return None

    found = await asyncio.gather(*(probe(cap, path) for cap, path in ENDPOINTS.items()))
    caps = sorted(x for x in found if x)
    if caps:
        return {"status": "partial", "source": "http-probe", "capabilities": caps,
                "message": "HTTPで確認できた機能のみ検出しました．未検出は機能なしを意味しません．"}
    return {"status": "unknown", "source": "none", "capabilities": [],
            "message": "API機能を確認できませんでした．手動指定できます． " + "; ".join(problems)[:180]}
