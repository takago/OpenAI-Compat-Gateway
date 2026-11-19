"""SQLite management store and authentication helpers for OpenAI-Compat-Gateway."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

CAPABILITIES = {"chat", "completions", "responses", "embeddings", "audio_transcriptions", "audio_translations", "audio_speech", "images_generations", "images_edits", "images_variations", "videos"}


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 350000)
    return f"pbkdf2_sha256$350000${salt.hex()}${digest.hex()}"


def password_check(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256" or int(rounds) < 100000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def validate_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment or not p.path.rstrip("/").endswith("/v1"):
        raise ValueError("Backend URL must be http(s)://hostname[:port]/v1 without credentials, query or fragment")
    return url.rstrip("/")


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS backends (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, capabilities TEXT NOT NULL,
                key_env TEXT, enabled INTEGER NOT NULL DEFAULT 1, discover INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS models (
                backend_id TEXT NOT NULL, upstream_id TEXT NOT NULL, public_id TEXT NOT NULL UNIQUE,
                published INTEGER NOT NULL DEFAULT 0, seen INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(backend_id, upstream_id),
                FOREIGN KEY(backend_id) REFERENCES backends(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                hash TEXT NOT NULL UNIQUE, capabilities TEXT NOT NULL, models TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, expires_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS video_jobs (
                public_id TEXT PRIMARY KEY, backend_id TEXT NOT NULL, owner_key INTEGER NOT NULL,
                upstream_id TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER NOT NULL,
                key_id INTEGER NOT NULL, capability TEXT NOT NULL, model TEXT,
                status INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_events(time);
            """)
            # Safe in-place schema update for existing SQLite databases.
            cols = {r[1] for r in db.execute("PRAGMA table_info(api_keys)")}
            if "expires_at" not in cols:
                db.execute("ALTER TABLE api_keys ADD COLUMN expires_at INTEGER")
        os.chmod(path, 0o600)

    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def bootstrap(self, config: dict):
        with self.db() as db:
            if db.execute("SELECT count(*) FROM backends").fetchone()[0]:
                return
            for b in config.get("backends", []):
                db.execute("INSERT INTO backends VALUES (?,?,?,?,1,?)", (b["id"], validate_url(b["base_url"]), json.dumps(b["capabilities"]), b.get("api_key_env"), int(b.get("discover_models", True))))
                for mid in b.get("models", []):
                    alias = mid
                    if db.execute("SELECT 1 FROM models WHERE public_id=?", (alias,)).fetchone():
                        alias = f'{b["id"]}::{mid}'
                    db.execute("INSERT INTO models VALUES (?,?,?,?,1)", (b["id"], mid, alias, 1))
            key = os.getenv("GATEWAY_API_KEY")
            if key:
                db.execute("INSERT INTO api_keys(name,hash,capabilities,models,created_at) VALUES(?,?,?,?,?)", ("initial-admin-api-key", hash_key(key), json.dumps(["*"]), json.dumps(["*"]), int(time.time())))

    def backends(self):
        with self.db() as db:
            return [dict(x) for x in db.execute("SELECT * FROM backends ORDER BY id")]

    def models(self):
        with self.db() as db:
            return [dict(x) for x in db.execute("SELECT models.*,backends.capabilities,backends.enabled AS backend_enabled FROM models JOIN backends ON backends.id=models.backend_id ORDER BY models.public_id")]

    def keys(self):
        with self.db() as db:
            return [{k:v for k,v in dict(x).items() if k != "hash"} for x in db.execute("SELECT * FROM api_keys ORDER BY id")]

    def key_for(self, secret: str):
        digest = hash_key(secret)
        with self.db() as db:
            row = db.execute("SELECT * FROM api_keys WHERE hash=? AND enabled=1 AND (expires_at IS NULL OR expires_at>?)", (digest, int(time.time()))).fetchone()
            return dict(row) if row else None

    def add_key(self, name: str, capabilities: list[str], models: list[str], expires_at: int | None = None):
        token = "ocg_" + secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("INSERT INTO api_keys(name,hash,capabilities,models,created_at,expires_at) VALUES(?,?,?,?,?,?)", (name, hash_key(token), json.dumps(capabilities), json.dumps(models), int(time.time()), expires_at))
        return token

    def allowed(self, key: dict, cap: str, model: str | None) -> bool:
        caps = json.loads(key["capabilities"])
        mods = json.loads(key["models"])
        return ("*" in caps or cap in caps) and (model is None or "*" in mods or model in mods)

    def discover(self, backend_id: str, model_ids: set[str]):
        with self.db() as db:
            db.execute("UPDATE models SET seen=0 WHERE backend_id=?", (backend_id,))
            for mid in sorted(model_ids):
                row = db.execute("SELECT 1 FROM models WHERE backend_id=? AND upstream_id=?", (backend_id, mid)).fetchone()
                if row:
                    db.execute("UPDATE models SET seen=1 WHERE backend_id=? AND upstream_id=?", (backend_id, mid))
                else:
                    public_id = mid
                    if db.execute("SELECT 1 FROM models WHERE public_id=?", (mid,)).fetchone():
                        public_id = f"{backend_id}::{mid}"
                    db.execute("INSERT INTO models VALUES (?,?,?,?,1)", (backend_id, mid, public_id, 0))

    def log(self, key_id: int, cap: str, model: str | None, status: int, elapsed_ms: int):
        with self.db() as db:
            db.execute("INSERT INTO usage_events(time,key_id,capability,model,status,elapsed_ms) VALUES (?,?,?,?,?,?)", (int(time.time()), key_id, cap, model, status, elapsed_ms))

    def add_admin_session(self, token: str):
        with self.db() as db:
            db.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (int(time.time()),))
            db.execute("INSERT INTO admin_sessions VALUES(?,?)", (hash_key(token), int(time.time())+28800))

    def valid_admin_session(self, token: str) -> bool:
        with self.db() as db:
            row = db.execute("SELECT 1 FROM admin_sessions WHERE token_hash=? AND expires_at>=?", (hash_key(token), int(time.time()))).fetchone()
            return row is not None

    def delete_admin_session(self, token: str):
        with self.db() as db:
            db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (hash_key(token),))

    def metrics(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT key_id, capability, COUNT(*) AS requests, ROUND(AVG(elapsed_ms)) AS average_ms FROM usage_events GROUP BY key_id,capability ORDER BY requests DESC")]
