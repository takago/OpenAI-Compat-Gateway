"""OpenAI-compatible API gateway with SQLite-backed backend/model/key management."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os

# Use the operating system CA certificate bundle for HTTPS backends.
os.environ["REQUESTS_CA_BUNDLE"] = "/etc/ssl/certs/ca-certificates.crt"
os.environ["SSL_CERT_FILE"] = "/etc/ssl/certs/ca-certificates.crt"
import re
import secrets
import time
from email.message import Message
from python_multipart import MultipartParser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, unquote_to_bytes, urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from management import CAPABILITIES, Store, password_check, validate_url, hash_key
from endpoints import ROUTES, ENDPOINTS, normalize

LOG = logging.getLogger("compat_gateway")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}
RETURN_HEADERS = {"content-type", "content-disposition", "content-encoding", "content-language", "content-location", "cache-control", "etag", "last-modified", "expires", "vary", "pragma", "age", "accept-ranges", "content-range", "location", "allow", "openai-processing-ms", "openai-version", "openai-poll-after-ms", "retry-after", "x-request-id"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def route_for(method: str, path: str):
    fixed = {"chat/completions":"chat", "completions":"completions", "responses":"responses", "embeddings":"embeddings", "audio/transcriptions":"audio_transcriptions", "audio/translations":"audio_translations", "audio/speech":"audio_speech", "images/generations":"images_generations", "images/edits":"images_edits", "images/variations":"images_variations", "videos":"videos"}
    if method == "POST" and path in fixed:
        return fixed[path], None
    m = re.fullmatch(r"videos/([^/]+)(?:/(content|remix))?", path)
    if m and (method in {"GET", "DELETE"} or (method == "POST" and m.group(2) == "remix")):
        return "videos", m.group(1)
    raise HTTPException(404, "Unsupported API endpoint")


def _media_type(content_type: str) -> str:
    """HTTP media types are case insensitive; parameters are not relevant here."""
    return content_type.split(";", 1)[0].strip().lower()


def _multipart_model(content_type: str, body: bytes) -> tuple[str, int, int] | None:
    """Locate one well-formed model field without altering uploaded files.

    python-multipart validates MIME boundaries.  All offsets refer to the
    original request bytes, so replacing the field preserves every other byte.
    """
    match = re.search(r'(?:^|;)\s*boundary\s*=\s*(?:"([^"\r\n]+)"|([^;\s]+))', content_type, re.I)
    if not match:
        raise HTTPException(400, "Multipart boundary missing")
    boundary = (match.group(1) or match.group(2)).encode("latin1")
    if not boundary or len(boundary) > 70:
        raise HTTPException(400, "Invalid multipart boundary")

    found: list[tuple[str, int, int]] = []
    field = bytearray()
    value = bytearray()
    headers: dict[str, str] = {}
    is_model = False
    offsets: list[tuple[int, int]] = []
    header_bytes = 0
    complete = False

    def on_header_field(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > 16384:
            raise HTTPException(400, "Multipart headers too large")
        field.extend(data[start:end])

    def on_header_value(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > 16384:
            raise HTTPException(400, "Multipart headers too large")
        value.extend(data[start:end])

    def on_header_end():
        name = field.decode('latin1').strip().lower()
        if name in headers:
            raise HTTPException(400, "Duplicate multipart header")
        headers[name] = value.decode('latin1').strip()
        field.clear()
        value.clear()

    def on_headers_finished():
        nonlocal is_model
        disposition = Message()
        disposition['Content-Disposition'] = headers.get('content-disposition', '')
        is_model = disposition.get_content_disposition() == 'form-data' and disposition.get_param('name', header='content-disposition') == 'model'
        if is_model and 'filename' in headers.get('content-disposition', '').lower():
            raise HTTPException(400, "Model must be a text field")
        if is_model and found:
            raise HTTPException(400, "Duplicate multipart model field")

    def on_part_data(data, start, end):
        if is_model:
            offsets.append((start, end))
            if sum(b-a for a,b in offsets) > 4096:
                raise HTTPException(400, "Model field too large")

    def on_part_end():
        nonlocal is_model, header_bytes
        if is_model:
            if not offsets:
                raise HTTPException(400, "Empty multipart model field")
            beginning, ending = offsets[0][0], offsets[-1][1]
            if any(a != previous[1] for previous, (a, b) in zip(offsets, offsets[1:])):
                raise HTTPException(400, "Invalid multipart model field")
            try:
                text = body[beginning:ending].decode('utf-8')
            except UnicodeError:
                raise HTTPException(400, "Invalid model encoding")
            if not text:
                raise HTTPException(400, "Empty multipart model field")
            found.append((text, beginning, ending))
        is_model = False
        offsets.clear()
        headers.clear()
        field.clear()
        value.clear()
        header_bytes = 0

    def on_end():
        nonlocal complete
        complete = True

    callbacks = {"on_header_field": on_header_field, "on_header_value": on_header_value,
                 "on_header_end": on_header_end, "on_headers_finished": on_headers_finished,
                 "on_part_data": on_part_data, "on_part_end": on_part_end,
                 "on_end": on_end}
    try:
        parser = MultipartParser(boundary, callbacks)
        parser.write(body)
        parser.finalize()
    except HTTPException:
        raise
    except (ValueError, UnicodeError, IndexError) as exc:
        raise HTTPException(400, "Invalid multipart request") from exc
    if not complete:
        raise HTTPException(400, "Incomplete multipart request")
    if len(found) > 1:
        raise HTTPException(400, "Duplicate multipart model field")
    return found[0] if found else None


def _parse_json_request(body: bytes):
    # Reject ambiguous model keys before authorizing access. JSON objects with
    # duplicate keys may be interpreted differently by separate parsers.
    def check_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key == "model" and key in obj:
                raise ValueError("Duplicate JSON model field")
            obj[key] = value
        return obj

    return json.loads(body, object_pairs_hook=check_pairs)


def extract_model(content_type: str, body: bytes) -> str | None:
    media = _media_type(content_type)
    if media == "application/json":
        try:
            value = _parse_json_request(body)
            return value.get("model") if isinstance(value, dict) and isinstance(value.get("model"), str) else None
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON request")
    if media == "multipart/form-data":
        part = _multipart_model(content_type, body)
        return part[0] if part else None
    return None


def replace_model(content_type: str, body: bytes, public_id: str, upstream_id: str) -> bytes:
    if public_id == upstream_id:
        return body
    media = _media_type(content_type)
    if media == "application/json":
        try:
            data = _parse_json_request(body)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, "Invalid JSON request") from exc
        if not isinstance(data, dict) or data.get("model") != public_id:
            raise HTTPException(400, "Model alias requires a matching model field")
        data["model"] = upstream_id
        return json.dumps(data, ensure_ascii=False).encode()
    if media == "multipart/form-data":
        part = _multipart_model(content_type, body)
        if part and part[0] == public_id:
            return body[:part[1]] + upstream_id.encode('utf-8') + body[part[2]:]
    raise HTTPException(400, "Model alias requires JSON or multipart model field")


def validate_proxy_path(raw_path: bytes) -> None:
    """Reject ambiguous paths before URL joining or upstream normalization.

    In particular, decoded dot segments, backslashes, and encoded slashes
    must not permit a dedicated key to escape the configured /v1 prefix.
    """
    if not raw_path.startswith(b'/v1/'):
        raise HTTPException(400, "Invalid API path")
    candidate = raw_path
    for _ in range(5):
        if b'\\' in candidate or any(segment in (b'.', b'..') for segment in candidate.split(b'/')):
            raise HTTPException(400, "Unsafe API path")
        if any(byte < 32 or byte == 127 for byte in candidate):
            raise HTTPException(400, "Unsafe API path")
        if re.search(rb'%(?![0-9A-Fa-f]{2})', candidate):
            raise HTTPException(400, "Invalid percent encoding in API path")
        if re.search(rb'%(?:2[fF]|5[cC])', candidate):
            raise HTTPException(400, "Encoded path separators are not allowed")
        decoded = unquote_to_bytes(candidate)
        if decoded == candidate:
            return
        candidate = decoded
    if b'%' in candidate:
        raise HTTPException(400, "Excessively encoded API path")
    if b'\\' in candidate or any(segment in (b'.', b'..') for segment in candidate.split(b'/')):
        raise HTTPException(400, "Unsafe API path")


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    """Enforce the limit while receiving, even without Content-Length."""
    chunks: list[bytes] = []
    count = 0
    async for chunk in request.stream():
        count += len(chunk)
        if count > max_bytes:
            raise HTTPException(413, "Request too large")
        chunks.append(chunk)
    return b''.join(chunks)


def session_create(secret: str):
    payload = f"{int(time.time())+28800}.{secrets.token_urlsafe(24)}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + sig


def session_verify(secret: str, token: str) -> bool:
    try:
        expiry, nonce, signature = token.split(".",2)
        if int(expiry) < time.time():
            return False
        payload = expiry + "." + nonce
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def session_csrf(secret: str, token: str) -> str:
    return hmac.new(secret.encode(), ("csrf:" + token).encode(), hashlib.sha256).hexdigest()


def create_app(config_path: str | None = None) -> FastAPI:
    # Configuration is managed in SQLite; no .env or config.json is required.
    config = {"model_cache_seconds": 60, "max_request_mb": 256}
    max_upload = config["max_request_mb"] * 1024 * 1024
    db_path = os.getenv("GATEWAY_DB", "/var/lib/openai-compat-gateway/gateway.sqlite3")
    store = Store(db_path)
    session_secret = store.setting("session_secret")
    admin_enabled = True
    cached: dict[str, float] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10,read=None,write=120,pool=10),follow_redirects=False)
        yield
        await app.state.client.aclose()

    app = FastAPI(title="OpenAI-Compat-Gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store

    def upstream_key(row):
        if row.get("api_key"):
            return row["api_key"]
        env = row.get("key_env")
        if env and not os.getenv(env):
            raise HTTPException(503, f"Missing backend API key environment: {env}")
        return os.getenv(env) if env else None

    async def refresh_one(row, force=False):
        if not row["enabled"]:
            return {"backend": row["id"], "status": "skipped", "message": "Disabled"}
        if not force and cached.get(row["id"],0) > time.monotonic():
            return {"backend": row["id"], "status": "cached"}
        if not row["discover"]:
            return {"backend": row["id"], "status": "skipped", "message": "Model discovery disabled"}
        try:
            headers = {"authorization":"Bearer " + upstream_key(row)} if upstream_key(row) else {}
            resp = await app.state.client.get(row["url"]+"/models",headers=headers,timeout=8)
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):
                raise ValueError("Expected JSON object containing data array (OpenAI /v1/models)")
            ids = {m["id"] for m in payload["data"] if isinstance(m,dict) and isinstance(m.get("id"),str) and m["id"]}
            store.discover(row["id"],ids)
            cached[row["id"]] = time.monotonic() + max(1,int(config.get("model_cache_seconds",60)))
            return {"backend": row["id"], "status": "ok", "models": len(ids)}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            LOG.warning("Discovery %s failed: %s",row["id"],e)
            return {"backend": row["id"], "status": "error", "message": str(e)[:300]}

    async def refresh_all(force=False):
        return await asyncio.gather(*(refresh_one(b,force) for b in store.backends()))

    def public_models(key):
        result=[]
        for m in store.models():
            if m["published"] and m["seen"] and m["backend_enabled"]:
                caps=json.loads(m["endpoints"])
                if any(store.allowed(key,cap,m["public_id"]) for cap in caps):
                    result.append({"id":m["public_id"],"object":"model","created":0,"owned_by":m["backend_id"]})
        return result

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        path=request.url.path
        raw_path = request.scope.get("raw_path", path.encode('utf-8'))
        if raw_path.startswith(b"/v1/") or path.startswith("/v1/"):
            try:
                validate_proxy_path(raw_path)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if path == "/healthz" or path.startswith("/admin"):
            return await call_next(request)
        if not path.startswith("/v1/"):
            return JSONResponse({"detail":"Not Found"},status_code=404)
        auth=request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error":{"message":"Unauthorized"}},status_code=401,headers={"WWW-Authenticate":"Bearer"})
        secret=auth[7:].strip()
        dedicated=store.dedicated_backend_for(secret)
        if dedicated is not None:
            request.state.dedicated_backend=dedicated
            request.state.key=None
        else:
            request.state.dedicated_backend=None
        key=None if dedicated is not None else store.key_for(secret)
        if key is None and dedicated is None:
            return JSONResponse({"error":{"message":"Unauthorized"}},status_code=401,headers={"WWW-Authenticate":"Bearer"})
        request.state.key=key
        length=request.headers.get("content-length")
        try:
            if length and int(length) > max_upload:
                return JSONResponse({"error":{"message":"Request too large"}},status_code=413)
        except ValueError:
            return JSONResponse({"error":{"message":"Invalid Content-Length"}},status_code=400)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {"status":"ok","backends":len(store.backends())}

    @app.get("/v1/models")
    async def models_api(request: Request):
        if getattr(request.state, "dedicated_backend", None) is not None:
            return await proxy("models", request)
        await refresh_all()
        return {"object":"list","data":public_models(request.state.key)}

    @app.api_route("/v1/{path:path}",methods=["GET","POST","PUT","DELETE"])
    async def proxy(path: str, request: Request):
        start=time.monotonic()
        key=request.state.key
        method=request.method
        dedicated=getattr(request.state, "dedicated_backend", None)
        if dedicated is None:
            cap, video_id = route_for(method,path)
        else:
            cap, video_id = method + " /v1/" + path, None
        body=await read_limited_body(request, max_upload)
        content_type=request.headers.get("content-type","")
        model = None if dedicated is not None or video_id else extract_model(content_type, body)
        upstream_id=None
        if dedicated is not None:
            # Dedicated credentials determine the destination for *all* /v1 paths.
            # Do not rewrite model, voice, body or query parameters.
            backend=dedicated
        elif video_id:
            with store.db() as db:
                job=db.execute("SELECT * FROM video_jobs WHERE public_id=?",(video_id,)).fetchone()
            if job is None:
                raise HTTPException(404,"Unknown video job")
            if job["owner_key"] != key["id"]:
                raise HTTPException(403,"Video job belongs to a different API key")
            if not store.allowed(key,"videos",None):
                raise HTTPException(403,"Not authorized for videos")
            backend=next((b for b in store.backends() if b["id"]==job["backend_id"] and b["enabled"]),None)
            if backend is None:
                raise HTTPException(503,"Video backend disabled")
            raw_id=job["upstream_id"]
            if not raw_id or "/" in raw_id or "\\" in raw_id:
                raise HTTPException(404,"Invalid upstream video ID")
            suffix="" if path==f"videos/{video_id}" else "/"+path[len(f"videos/{video_id}/"):]
            path="videos/"+quote(raw_id,safe="")+suffix
        else:
            await refresh_all()
            candidates=[]
            for m in store.models():
                if m["published"] and m["seen"] and m["backend_enabled"] and ROUTES[cap] in json.loads(m["endpoints"]):
                    if model is None or m["public_id"]==model:
                        candidates.append(m)
            if model is not None:
                if not store.allowed(key,cap,model):
                    raise HTTPException(403,"Model or capability not permitted")
                if not candidates:
                    raise HTTPException(404,"Model not found or unpublished")
            else:
                candidates=[m for m in candidates if store.allowed(key,cap,m["public_id"])]
                if not candidates:
                    raise HTTPException(403,"Capability not permitted")
            if len(candidates)!=1:
                raise HTTPException(400,"Specify a model to select a backend")
            selected=candidates[0]
            upstream_id=selected["upstream_id"]
            backend=next(b for b in store.backends() if b["id"]==selected["backend_id"])
            if model is not None:
                body=replace_model(content_type,body,model,upstream_id)
        headers={k:v for k,v in request.headers.items() if k.lower() not in HOP | {"authorization","x-api-key","cookie","accept-encoding"}}
        if upstream_key(backend):
            headers["authorization"]="Bearer "+upstream_key(backend)
        headers["accept-encoding"]="identity"
        url=backend["url"]+"/"+path
        if request.url.query:
            url += "?" + request.url.query
        try:
            req=app.state.client.build_request(method,url,content=body if body else None,headers=headers)
            upstream=await app.state.client.send(req,stream=True)
        except httpx.HTTPError as exc:
            store.log(key["id"] if key is not None else dedicated["dedicated_key_id"],cap,model,502,int((time.monotonic()-start)*1000))
            LOG.warning("Upstream %s failed: %s",backend["id"],exc)
            raise HTTPException(502,"Upstream unavailable") from exc
        store.log(key["id"] if key is not None else dedicated["dedicated_key_id"],cap,model,upstream.status_code,int((time.monotonic()-start)*1000))
        resp_headers={k:v for k,v in upstream.headers.items() if k.lower() in RETURN_HEADERS}
        # Redirects back to the same upstream API must keep the client on the gateway.
        if 'location' in resp_headers:
            target = urlsplit(resp_headers['location'])
            backend_base = urlsplit(backend['url'])
            if (target.scheme, target.netloc) == (backend_base.scheme, backend_base.netloc) and (
                target.path == backend_base.path or target.path.startswith(backend_base.path.rstrip('/') + '/')):
                public_base = urlsplit(str(request.base_url))
                upstream_prefix = backend_base.path.rstrip('/')
                suffix = target.path[len(upstream_prefix):]
                resp_headers['location'] = urlunsplit((public_base.scheme, public_base.netloc,
                    '/v1' + suffix, target.query, target.fragment))
        if cap=="videos" and upstream.is_success and ((method=="POST" and (video_id is None or path.endswith("/remix"))) or (method=="GET" and video_id is not None and not path.endswith("/content"))):
            try:
                data=await upstream.aread()
                try:
                    obj=json.loads(data)
                except (ValueError,UnicodeError):
                    return Response(content=data,status_code=upstream.status_code,headers=resp_headers)
                if isinstance(obj,dict) and isinstance(obj.get("id"),str):
                    raw=obj["id"]
                    public="gw."+backend["id"]+"."+secrets.token_urlsafe(18) if method=="POST" else video_id
                    if method=="POST":
                        with store.db() as db:
                            db.execute("INSERT INTO video_jobs VALUES (?,?,?,?,?)",(public,backend["id"],key["id"],raw,int(time.time())))
                    obj["id"]=public
                    resp_headers.pop("etag",None)
                    resp_headers.pop("content-type",None)
                    return JSONResponse(content=obj,status_code=upstream.status_code,headers=resp_headers)
                return Response(content=data,status_code=upstream.status_code,headers=resp_headers)
            finally:
                await upstream.aclose()
        # MockTransport and some custom transports return an already-buffered response.
        # Real network streams use raw bytes to preserve Content-Encoding unchanged.
        if upstream.is_stream_consumed:
            resp_headers.pop("content-encoding", None)
            response_body = upstream.aiter_bytes()
        else:
            response_body = upstream.aiter_raw()
        return StreamingResponse(response_body,status_code=upstream.status_code,headers=resp_headers,background=BackgroundTask(upstream.aclose))

    def admin_guard(request: Request,mutate=False):
        if not admin_enabled:
            raise HTTPException(404,"Admin disabled")
        token=request.cookies.get("ocg_session","")
        if not session_verify(session_secret,token) or not store.valid_admin_session(token):
            raise HTTPException(401,"Administrator login required")
        if mutate and not hmac.compare_digest(request.headers.get("x-csrf-token",""),session_csrf(session_secret,token)):
            raise HTTPException(403,"CSRF token invalid")

    @app.get("/admin",response_class=HTMLResponse)
    async def admin_page():
        if not admin_enabled:
            raise HTTPException(404,"Admin disabled")
        return HTMLResponse(Path(__file__).with_name("admin.html").read_text(encoding="utf8"),headers={"Cache-Control":"no-store"})

    @app.get("/admin/api/setup-status")
    async def setup_status():
        return {"needs_setup": not store.admin_initialized()}

    @app.post("/admin/api/setup")
    async def first_setup(request: Request):
        # Deliberately reachable via the public /admin path until the first admin is created.
        # Protect the gateway with Caddy/network ACL until setup finishes.
        if store.admin_initialized():
            raise HTTPException(409,"Administrator already initialized")
        try:
            data = await request.json()
            password = data.get("password")
            if not isinstance(password,str) or not store.create_admin(password):
                raise HTTPException(400,"Password must be at least 12 characters")
        except (ValueError,TypeError):
            raise HTTPException(400,"Invalid administrator password")
        return {"ok": True}

    @app.post("/admin/api/login")
    async def login(request:Request):
        if not admin_enabled:
            raise HTTPException(404,"Admin disabled")
        data=await request.json()
        stored_hash = store.setting("admin_password_hash")
        if not stored_hash or not password_check(str(data.get("password","")),stored_hash):
            await asyncio.sleep(.3)
            raise HTTPException(401,"Incorrect password")
        token=session_create(session_secret)
        store.add_admin_session(token)
        response=JSONResponse({"ok":True,"csrf":session_csrf(session_secret,token)})
        response.set_cookie("ocg_session",token,httponly=True,secure=os.getenv("ADMIN_COOKIE_SECURE","true").lower()=="true",samesite="strict",max_age=28800,path="/admin")
        return response

    @app.get("/admin/api/state")
    async def admin_state(request:Request):
        admin_guard(request)
        token=request.cookies.get("ocg_session","")
        return {"csrf":session_csrf(session_secret,token),"endpoints":sorted(ENDPOINTS),"backends":[{k:v for k,v in b.items() if k not in {"api_key", "dedicated_key_hash"}} for b in store.backends()],"models":store.models(),"keys":store.keys(),"metrics":store.metrics()}

    @app.post("/admin/api/logout")
    async def logout(request:Request):
        admin_guard(request,True)
        store.delete_admin_session(request.cookies.get("ocg_session",""))
        response=JSONResponse({"ok":True})
        response.delete_cookie("ocg_session",path="/admin")
        return response

    @app.post("/admin/api/discover")
    async def discover(request:Request):
        admin_guard(request,True)
        results = await refresh_all(True)
        return {"ok":True, "results":results}

    @app.post("/admin/api/backends")
    async def save_backend(request:Request):
        admin_guard(request,True)
        data=await request.json()
        bid=data.get("id","")
        if not isinstance(bid,str) or not ID_RE.fullmatch(bid):
            raise HTTPException(400,"Invalid backend id")
        try:
            url=validate_url(data["url"])
        except (ValueError,KeyError) as exc:
            raise HTTPException(400,str(exc))
        kind=data.get("kind", "openai")
        if kind not in {"openai", "irodori", "kokoro"}:
            raise HTTPException(400, "Invalid backend kind")
        direct_key=data.get("api_key", "")
        clear_key = data.get("clear_api_key", False)
        if not isinstance(direct_key,str) or len(direct_key)>4096 or not isinstance(clear_key,bool):
            raise HTTPException(400,"Invalid backend API key")
        if clear_key and direct_key:
            raise HTTPException(400, "Cannot replace and clear the backend key together")
        with store.db() as db:
            current=db.execute("SELECT kind,url,api_key FROM backends WHERE id=?",(bid,)).fetchone()
            old_kind=current["kind"] if current is not None else None
            if current and current['url'] != url and current['api_key'] and not clear_key and not direct_key:
                raise HTTPException(409, "When changing the backend URL, replace or clear its saved API key")
            discover_enabled=int(bool(data.get("discover",True)))
            db.execute("""INSERT INTO backends(id,url,capabilities,key_env,enabled,discover,api_key,kind,dedicated_key_hash)
                VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET
                url=excluded.url,capabilities=excluded.capabilities,
                key_env=NULL,enabled=excluded.enabled,discover=excluded.discover,
                api_key=CASE WHEN ? THEN NULL WHEN excluded.api_key='' THEN backends.api_key ELSE excluded.api_key END,
                kind=excluded.kind,dedicated_key_hash=NULL""",
                (bid,url,json.dumps([]),None,int(bool(data.get("enabled",True))),discover_enabled,direct_key,kind,int(clear_key)))
            # Switching back to OpenAI revokes dedicated credentials. Switching
            # between dedicated types leaves their independently issued keys intact.
            if kind == "openai" and old_kind in {"irodori", "kokoro"}:
                db.execute("DELETE FROM api_keys WHERE backend_id=?", (bid,))
            # Model rows are retained for later return to OpenAI mode.
        cached.pop(bid,None)
        result = await refresh_one(next(b for b in store.backends() if b["id"] == bid), True)
        return {"ok":True,"discovery":result}

    @app.delete("/admin/api/backends/{backend_id}")
    async def remove_backend(backend_id:str,request:Request):
        admin_guard(request,True)
        with store.db() as db:
            db.execute("DELETE FROM api_keys WHERE backend_id=?",(backend_id,))
            db.execute("DELETE FROM backends WHERE id=?",(backend_id,))
        cached.pop(backend_id,None)
        return {"ok":True}

    @app.post("/admin/api/models")
    async def add_manual_model(request: Request):
        admin_guard(request, True)
        data = await request.json()
        bid = data.get("backend_id")
        upstream = data.get("upstream_id")
        public = data.get("public_id") or upstream
        if not isinstance(bid, str) or not ID_RE.fullmatch(bid):
            raise HTTPException(400, "Invalid backend ID")
        if not isinstance(upstream, str) or not upstream.strip() or len(upstream) > 200:
            raise HTTPException(400, "Invalid upstream model ID")
        if not isinstance(public, str) or not public.strip() or len(public) > 200:
            raise HTTPException(400, "Invalid public model ID")
        try:
            endpoints = normalize(data.get("endpoints", []))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        try:
            with store.db() as db:
                backend_row=db.execute("SELECT kind FROM backends WHERE id=?", (bid,)).fetchone()
                if backend_row is None:
                    raise HTTPException(404, "Backend not found")
                db.execute("""INSERT INTO models(backend_id,upstream_id,public_id,published,seen,manual,endpoints)
                              VALUES(?,?,?,?,1,1,?)""",
                           (bid, upstream, public, int(bool(data.get("published", False))),json.dumps(endpoints)))
        except __import__("sqlite3").IntegrityError:
            raise HTTPException(409, "Model ID already exists; edit the existing model instead")
        return {"ok": True}

    @app.delete("/admin/api/models")
    async def delete_manual_model(request: Request):
        admin_guard(request, True)
        data = await request.json()
        bid, upstream = data.get("backend_id"), data.get("upstream_id")
        with store.db() as db:
            row = db.execute("SELECT manual FROM models WHERE backend_id=? AND upstream_id=?", (bid, upstream)).fetchone()
            if row is None:
                raise HTTPException(404, "Model not found")
            db.execute("DELETE FROM models WHERE backend_id=? AND upstream_id=?", (bid, upstream))
        return {"ok": True}

    @app.patch("/admin/api/models")
    async def change_model(request:Request):
        admin_guard(request,True)
        data=await request.json()
        bid=data.get("backend_id")
        upstream=data.get("upstream_id")
        public=data.get("public_id")
        if public is not None and (not isinstance(public,str) or not public or len(public)>200):
            raise HTTPException(400,"Invalid public model ID")
        try:
            endpoints = normalize(data.get("endpoints", [])) if "endpoints" in data else None
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        try:
            with store.db() as db:
                current=db.execute("SELECT * FROM models WHERE backend_id=? AND upstream_id=?",(bid,upstream)).fetchone()
                if not current:
                    raise HTTPException(404,"Model not found")
                if public is None:
                    public = current["public_id"]
                values = json.dumps(endpoints) if endpoints is not None else current["endpoints"]
                hidden = int(bool(data["hidden"])) if "hidden" in data else current["hidden"]
                cur=db.execute("UPDATE models SET public_id=?,published=?,endpoints=?,hidden=? WHERE backend_id=? AND upstream_id=?",(public,int(bool(data.get("published",current["published"]))),values,hidden,bid,upstream))
                if not cur.rowcount:
                    raise HTTPException(404,"Model not found")
        except __import__("sqlite3").IntegrityError:
            raise HTTPException(409,"Public model ID already exists")
        return {"ok":True}

    @app.patch("/admin/api/models/batch")
    async def change_models_batch(request: Request):
        admin_guard(request, True)
        data=await request.json()
        if not isinstance(data,list) or len(data)>2000:
            raise HTTPException(400,"Expected model updates array")
        validated=[]
        seen=set()
        for item in data:
            if not isinstance(item,dict):
                raise HTTPException(400,"Invalid model update")
            bid, upstream, public = item.get("backend_id"),item.get("upstream_id"),item.get("public_id")
            if not all(isinstance(x,str) and x for x in (bid,upstream,public)) or len(public)>200:
                raise HTTPException(400,"Invalid model ID")
            pair=(bid,upstream)
            if pair in seen:
                raise HTTPException(400,"Duplicate model update")
            seen.add(pair)
            try:
                endpoints=normalize(item.get("endpoints",[]))
            except ValueError as exc:
                raise HTTPException(400,str(exc))
            validated.append((bid,upstream,public,int(bool(item.get("published",False))),json.dumps(endpoints)))
        try:
            with store.db() as db:
                for bid,upstream,public,published,endpoints in validated:
                    if db.execute("SELECT 1 FROM models WHERE backend_id=? AND upstream_id=?",(bid,upstream)).fetchone() is None:
                        raise HTTPException(404,"Unknown model: "+bid+" / "+upstream)
                    # Handle swapping aliases in two phases to prevent temporary UNIQUE conflicts.
                # Public aliases must not collide with other models, including unmodified rows.
                wanted=[v[2] for v in validated]
                if len(set(wanted)) != len(wanted):
                    raise HTTPException(409,"Duplicate public model IDs")
                for public in wanted:
                    row=db.execute("SELECT backend_id,upstream_id FROM models WHERE public_id=?",(public,)).fetchone()
                    if row and (row["backend_id"],row["upstream_id"]) not in seen:
                        raise HTTPException(409,"Public model ID already exists: "+public)
                for bid,upstream,public,published,endpoints in validated:
                    db.execute("UPDATE models SET public_id=? WHERE backend_id=? AND upstream_id=?",(secrets.token_urlsafe(24),bid,upstream))
                for bid,upstream,public,published,endpoints in validated:
                    db.execute("UPDATE models SET public_id=?,published=?,endpoints=? WHERE backend_id=? AND upstream_id=?",(public,published,endpoints,bid,upstream))
        except __import__("sqlite3").IntegrityError:
            raise HTTPException(409,"Public model ID already exists")
        return {"ok":True,"updated":len(validated)}

    def parse_expiration(value):
        # API contract: null means never expires; integer means Unix seconds (UTC).
        if value is None:
            return None
        if type(value) is not int or not (0 < value <= 253402300799):
            raise HTTPException(400, "expires_at must be a Unix timestamp (seconds) or null")
        if value <= int(time.time()):
            raise HTTPException(400, "expires_at must be in the future")
        return value

    @app.post("/admin/api/keys")
    async def add_key(request:Request):
        admin_guard(request,True)
        data=await request.json()
        name=data.get("name","")
        if not isinstance(name,str) or not name.strip() or len(name)>100:
            raise HTTPException(400,"Invalid key name")
        name=name.strip()
        backend_id=data.get("backend_id")
        expires_at=parse_expiration(data.get("expires_at"))
        if backend_id is not None:
            if not isinstance(backend_id,str):
                raise HTTPException(400,"Invalid backend id")
            with store.db() as db:
                backend=db.execute("SELECT kind FROM backends WHERE id=?",(backend_id,)).fetchone()
            if backend is None or backend["kind"] not in {"irodori","kokoro"}:
                raise HTTPException(400,"Select an Irodori-TTS or Kokoro-TTS backend")
            caps,models=[],[]
        else:
            caps=data.get("capabilities",[])
            models=data.get("models",[])
            if not isinstance(caps,list) or not caps or not all(isinstance(c,str) for c in caps) or not set(caps)<=CAPABILITIES|ENDPOINTS|{"*"} or not isinstance(models,list) or not models or not all(isinstance(m,str) for m in models):
                raise HTTPException(400,"Invalid key settings")
            caps=normalize(caps)
        try:
            token=store.add_key(name,caps,models,expires_at,backend_id)
        except __import__("sqlite3").IntegrityError:
            raise HTTPException(409,"Key name exists")
        return {"key":token,"message":"Copy this key now: it will not be shown again"}

    @app.delete("/admin/api/keys/{key_id}")
    async def delete_key(key_id:int,request:Request):
        admin_guard(request,True)
        with store.db() as db:
            result=db.execute("DELETE FROM api_keys WHERE id=?",(key_id,))
            if not result.rowcount:
                raise HTTPException(404,"Unknown key")
        return {"ok":True}

    @app.post("/admin/api/keys/{key_id}/rotate")
    async def rotate_key(key_id:int,request:Request):
        admin_guard(request,True)
        # Revoke the old secret immediately, retaining key permissions and usage history.
        from management import hash_key
        token="ocg_" + secrets.token_urlsafe(32)
        with store.db() as db:
            result=db.execute("UPDATE api_keys SET hash=? WHERE id=?",(hash_key(token),key_id))
            if not result.rowcount:
                raise HTTPException(404,"Unknown key")
        return {"key":token,"message":"Old key revoked. Copy the new key now."}

    @app.patch("/admin/api/keys/{key_id}")
    async def change_key(key_id:int,request:Request):
        admin_guard(request,True)
        data=await request.json()
        caps=data.get("capabilities")
        models=data.get("models")
        if caps is not None and (not isinstance(caps,list) or not caps or not set(caps)<=CAPABILITIES|ENDPOINTS|{"*"}):
            raise HTTPException(400,"Invalid capabilities")
        if models is not None and (not isinstance(models,list) or not models or not all(isinstance(x,str) for x in models)):
            raise HTTPException(400,"Invalid model restrictions")
        with store.db() as db:
            row=db.execute("SELECT * FROM api_keys WHERE id=?",(key_id,)).fetchone()
            if row is None:
                raise HTTPException(404,"Unknown key")
            if row["backend_id"] is not None and (caps is not None or models is not None):
                raise HTTPException(400,"Dedicated key permissions are fixed to the backend")
            expires_at = parse_expiration(data["expires_at"]) if "expires_at" in data else row["expires_at"]
            db.execute("UPDATE api_keys SET enabled=?,capabilities=?,models=?,expires_at=? WHERE id=?",(int(bool(data.get("enabled",row["enabled"]))),json.dumps(normalize(caps) if caps is not None else json.loads(row["capabilities"])),json.dumps(models if models is not None else json.loads(row["models"])),expires_at,key_id))
        return {"ok":True}

    @app.get("/admin/api/backup")
    async def download_backup(request: Request):
        admin_guard(request)
        import sqlite3, tempfile
        from fastapi.responses import FileResponse
        fd, path=tempfile.mkstemp(suffix=".sqlite3",prefix="ocg-backup-")
        os.close(fd)
        try:
            with store.db() as source, sqlite3.connect(path) as destination:
                source.backup(destination)
        except Exception:
            os.unlink(path)
            raise
        return FileResponse(path, filename="openai-compat-gateway.sqlite3", media_type="application/octet-stream", background=BackgroundTask(lambda: os.path.exists(path) and os.unlink(path)))

    @app.post("/admin/api/restore")
    async def restore_backup(request: Request):
        admin_guard(request,True)
        import sqlite3, tempfile
        # Password is required again for this potentially destructive operation.
        password=request.headers.get("x-admin-password","")
        if not password_check(password,store.setting("admin_password_hash") or ""):
            raise HTTPException(403,"Administrator password required")
        raw=await request.body()
        if len(raw)>64*1024*1024:
            raise HTTPException(413,"Backup too large")
        fd,path=tempfile.mkstemp(suffix=".sqlite3",prefix="ocg-restore-")
        try:
            with os.fdopen(fd,"wb") as f: f.write(raw)
            with sqlite3.connect(path) as source:
                if source.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("Invalid SQLite backup")
                required={"settings","backends","models","api_keys","video_jobs"}
                existing={x[0] for x in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required<=existing or not source.execute("SELECT value FROM settings WHERE name='admin_password_hash'").fetchone():
                    raise ValueError("Not a valid gateway backup")
                # Restore atomically via SQLite backup API; original DB is preserved on validation errors.
                with store.db() as destination:
                    source.backup(destination)
            cached.clear()
        except (sqlite3.DatabaseError,ValueError) as exc:
            raise HTTPException(400,str(exc))
        finally:
            os.unlink(path)
        # Existing sessions may be invalid after restore; a service restart is recommended.
        return {"ok":True,"message":"Restored. Restart the gateway service before further use."}

    return app


app = create_app() if __name__ != "__main__" else None
