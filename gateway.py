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
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from management import CAPABILITIES, Store, password_check, validate_url
from endpoints import ROUTES, ENDPOINTS, normalize

LOG = logging.getLogger("compat_gateway")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}
RETURN_HEADERS = {"content-type", "content-disposition", "cache-control", "etag", "last-modified", "openai-processing-ms", "openai-version", "openai-poll-after-ms", "retry-after", "x-request-id"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def route_for(method: str, path: str):
    fixed = {"chat/completions":"chat", "completions":"completions", "responses":"responses", "embeddings":"embeddings", "audio/transcriptions":"audio_transcriptions", "audio/translations":"audio_translations", "audio/speech":"audio_speech", "images/generations":"images_generations", "images/edits":"images_edits", "images/variations":"images_variations", "videos":"videos"}
    if method == "POST" and path in fixed:
        return fixed[path], None
    m = re.fullmatch(r"videos/([^/]+)(?:/(content|remix))?", path)
    if m and (method in {"GET", "DELETE"} or (method == "POST" and m.group(2) == "remix")):
        return "videos", m.group(1)
    raise HTTPException(404, "Unsupported API endpoint")


def extract_model(content_type: str, body: bytes) -> str | None:
    if content_type.lower().startswith("application/json"):
        try:
            value = json.loads(body)
            return value.get("model") if isinstance(value, dict) and isinstance(value.get("model"), str) else None
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON request")
    if content_type.lower().startswith("multipart/form-data"):
        m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type, re.I)
        if not m:
            raise HTTPException(400, "Multipart boundary missing")
        delimiter = b"--" + (m.group(1) or m.group(2)).encode()
        for item in body.split(delimiter):
            sep = item.find(b"\r\n\r\n")
            if sep < 0:
                continue
            header = item[:sep].decode("latin1")
            if re.search(r'\bname="model"(?:\s*;|\s*$)', header, re.I):
                return item[sep+4:].split(b"\r\n",1)[0].decode("utf8", "replace")
    return None


def replace_model(content_type: str, body: bytes, public_id: str, upstream_id: str) -> bytes:
    if public_id == upstream_id:
        return body
    if content_type.startswith("application/json"):
        data = json.loads(body)
        data["model"] = upstream_id
        return json.dumps(data, ensure_ascii=False).encode()
    if content_type.startswith("multipart/form-data"):
        m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type, re.I)
        if not m:
            raise HTTPException(400, "Multipart boundary missing")
        marker = b"--" + (m.group(1) or m.group(2)).encode()
        chunks = body.split(marker)
        for idx, chunk in enumerate(chunks):
            sep = chunk.find(b"\r\n\r\n")
            if sep >= 0 and re.search(r'\bname="model"(?:\s*;|\s*$)',chunk[:sep].decode("latin1"),re.I):
                old = chunk[sep+4:].split(b"\r\n",1)[0]
                if old.decode("utf8", "replace") == public_id:
                    chunks[idx] = chunk[:sep+4] + upstream_id.encode() + chunk[sep+4+len(old):]
                    return marker.join(chunks)
    raise HTTPException(400, "Model alias requires JSON or multipart model field")


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
        if path == "/healthz" or path.startswith("/admin"):
            return await call_next(request)
        if not path.startswith("/v1/"):
            return JSONResponse({"detail":"Not Found"},status_code=404)
        auth=request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error":{"message":"Unauthorized"}},status_code=401,headers={"WWW-Authenticate":"Bearer"})
        key=store.key_for(auth[7:])
        if key is None:
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
        await refresh_all()
        return {"object":"list","data":public_models(request.state.key)}

    @app.api_route("/v1/{path:path}",methods=["GET","POST","DELETE"])
    async def proxy(path: str, request: Request):
        start=time.monotonic()
        key=request.state.key
        method=request.method
        cap, video_id = route_for(method,path)
        body=await request.body()
        if len(body)>max_upload:
            raise HTTPException(413,"Request too large")
        content_type=request.headers.get("content-type","")
        model=extract_model(content_type,body) if not video_id else None
        upstream_id=None
        if video_id:
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
            url += "?"+request.url.query
        try:
            req=app.state.client.build_request(method,url,content=body if method not in {"GET","DELETE"} else None,headers=headers)
            upstream=await app.state.client.send(req,stream=True)
        except httpx.HTTPError as exc:
            store.log(key["id"],cap,model,502,int((time.monotonic()-start)*1000))
            LOG.warning("Upstream %s failed: %s",backend["id"],exc)
            raise HTTPException(502,"Upstream unavailable") from exc
        store.log(key["id"],cap,model,upstream.status_code,int((time.monotonic()-start)*1000))
        resp_headers={k:v for k,v in upstream.headers.items() if k.lower() in RETURN_HEADERS}
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
        return StreamingResponse(upstream.aiter_bytes(),status_code=upstream.status_code,headers=resp_headers,background=BackgroundTask(upstream.aclose))

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
        return {"csrf":session_csrf(session_secret,token),"endpoints":sorted(ENDPOINTS),"backends":[{k:v for k,v in b.items() if k != "api_key"} for b in store.backends()],"models":store.models(),"keys":store.keys(),"metrics":store.metrics()}

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
        # API capabilities belong to individual models, never to backends.
        caps=[]
        direct_key=data.get("api_key", "")
        if not isinstance(direct_key,str) or len(direct_key)>4096:
            raise HTTPException(400,"Invalid backend API key")
        with store.db() as db:
            db.execute("""INSERT INTO backends(id,url,capabilities,key_env,enabled,discover,api_key)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                url=excluded.url,capabilities=excluded.capabilities,
                key_env=NULL,enabled=excluded.enabled,discover=excluded.discover,
                api_key=CASE WHEN excluded.api_key='' THEN backends.api_key ELSE excluded.api_key END""",
                (bid,url,json.dumps(caps),None,int(bool(data.get("enabled",True))),int(bool(data.get("discover",True))),direct_key))
        cached.pop(bid,None)
        result = await refresh_one(next(b for b in store.backends() if b["id"] == bid), True)
        return {"ok":True, "discovery":result}

    @app.delete("/admin/api/backends/{backend_id}")
    async def remove_backend(backend_id:str,request:Request):
        admin_guard(request,True)
        with store.db() as db:
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
                if db.execute("SELECT 1 FROM backends WHERE id=?", (bid,)).fetchone() is None:
                    raise HTTPException(404, "Backend not found")
                db.execute("DELETE FROM excluded_models WHERE backend_id=? AND upstream_id=?", (bid, upstream))
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
            if not row["manual"]:
                # Keep a tombstone so discovery cannot re-add a deliberately removed model.
                db.execute("INSERT OR IGNORE INTO excluded_models(backend_id,upstream_id) VALUES (?,?)", (bid, upstream))
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
        name=data.get("name","").strip()
        caps=data.get("capabilities",[])
        models=data.get("models",[])
        if not name or len(name)>100 or not isinstance(caps,list) or not caps or not set(caps)<=CAPABILITIES|ENDPOINTS|{"*"} or not isinstance(models,list) or not models or not all(isinstance(m,str) for m in models):
            raise HTTPException(400,"Invalid key settings")
        try:
            token=store.add_key(name,normalize(caps),models,parse_expiration(data.get("expires_at")))
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
            expires_at = parse_expiration(data["expires_at"]) if "expires_at" in data else row["expires_at"]
            db.execute("UPDATE api_keys SET enabled=?,capabilities=?,models=?,expires_at=? WHERE id=?",(int(bool(data.get("enabled",row["enabled"]))),json.dumps(normalize(caps) if caps is not None else json.loads(row["capabilities"])),json.dumps(models if models is not None else json.loads(row["models"])),expires_at,key_id))
        return {"ok":True}

    @app.post("/admin/api/backends/{backend_id}/diagnose")
    async def diagnose_backend(backend_id: str, request: Request):
        admin_guard(request, True)
        row=next((b for b in store.backends() if b["id"]==backend_id),None)
        if row is None:
            raise HTTPException(404,"Backend not found")
        result={"backend":backend_id,"checks":[]}
        for suffix in ("/models", "/healthz"):
            url=row["url"]+suffix if suffix=="/models" else row["url"][:-3]+suffix
            try:
                key=upstream_key(row)
                response=await app.state.client.get(url,headers={"authorization":"Bearer "+key} if key else {},timeout=6)
                status=response.status_code
                description=("OK" if 200<=status<300 else "Authentication required" if status in (401,403) else "Endpoint not available" if status==404 else "HTTP "+str(status))
                result["checks"].append({"path":suffix,"status":status,"message":description})
            except httpx.ConnectError as exc:
                result["checks"].append({"path":suffix,"status":None,"message":"TLS / connection error: "+str(exc)[:180]})
            except httpx.TimeoutException:
                result["checks"].append({"path":suffix,"status":None,"message":"Timeout"})
            except httpx.HTTPError as exc:
                result["checks"].append({"path":suffix,"status":None,"message":str(exc)[:180]})
        return result

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
