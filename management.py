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
from endpoints import ROUTES, ENDPOINTS, normalize

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
            CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
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
            # Older versions persisted deleted discoveries as tombstones.
            # Dropping only this obsolete table lets a future explicit refresh
            # re-import those models without changing any other model settings.
            db.execute("DROP TABLE IF EXISTS excluded_models")
            backend_cols = {r[1] for r in db.execute("PRAGMA table_info(backends)")}
            if "api_key" not in backend_cols:
                db.execute("ALTER TABLE backends ADD COLUMN api_key TEXT")
            if "auto_capabilities" not in backend_cols:
                db.execute("ALTER TABLE backends ADD COLUMN auto_capabilities INTEGER NOT NULL DEFAULT 0")
            if "detected_capabilities" not in backend_cols:
                db.execute("ALTER TABLE backends ADD COLUMN detected_capabilities TEXT NOT NULL DEFAULT '[]'")
            if "detection_status" not in backend_cols:
                db.execute("ALTER TABLE backends ADD COLUMN detection_status TEXT NOT NULL DEFAULT 'pending'")
            if "detection_source" not in backend_cols:
                db.execute("ALTER TABLE backends ADD COLUMN detection_source TEXT NOT NULL DEFAULT ''")
            model_cols = {r[1] for r in db.execute("PRAGMA table_info(models)")}
            if "manual" not in model_cols:
                db.execute("ALTER TABLE models ADD COLUMN manual INTEGER NOT NULL DEFAULT 0")
            if "endpoints" not in model_cols:
                db.execute("ALTER TABLE models ADD COLUMN endpoints TEXT NOT NULL DEFAULT '[]'")
                # Preserve existing routing behavior when migrating from backend-level capabilities.
                db.execute("""UPDATE models SET endpoints =
                    (SELECT backends.capabilities FROM backends WHERE backends.id=models.backend_id)""")
            if "hidden" not in model_cols:
                db.execute("ALTER TABLE models ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
            # Normalize both legacy backend capabilities and API-key permissions to endpoint IDs.
            for table, field, ident in (("models", "endpoints", "rowid"), ("api_keys", "capabilities", "id")):
                for row in db.execute(f"SELECT {ident},{field} FROM {table}").fetchall():
                    try:
                        value = json.dumps(normalize(json.loads(row[field])))
                    except (ValueError, TypeError):
                        continue
                    if value != row[field]:
                        db.execute(f"UPDATE {table} SET {field}=? WHERE {ident}=?", (value,row[ident]))
            # Safe in-place schema update for existing SQLite databases.
            cols = {r[1] for r in db.execute("PRAGMA table_info(api_keys)")}
            if "expires_at" not in cols:
                db.execute("ALTER TABLE api_keys ADD COLUMN expires_at INTEGER")
        os.chmod(path, 0o600)
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO settings(name,value) VALUES('session_secret',?)", (secrets.token_urlsafe(48),))

    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def setting(self, name: str) -> str | None:
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
            return row[0] if row else None

    def admin_initialized(self) -> bool:
        return self.setting("admin_password_hash") is not None

    def create_admin(self, password: str) -> bool:
        if len(password) < 12 or len(password) > 1024:
            raise ValueError("Password must be between 12 and 1024 characters")
        with self.db() as db:
            # Atomic: concurrent first-run requests cannot overwrite administrator.
            result = db.execute("INSERT OR IGNORE INTO settings(name,value) VALUES('admin_password_hash',?)", (password_hash(password),))
            return result.rowcount == 1

    def backends(self):
        with self.db() as db:
            rows = [dict(x) for x in db.execute("SELECT * FROM backends ORDER BY id")]
            for row in rows:
                found = db.execute("SELECT endpoints FROM models WHERE backend_id=?", (row["id"],)).fetchall()
                row["capabilities"] = json.dumps(sorted(set(e for r in found for e in json.loads(r["endpoints"]))))
            return rows

    def models(self):
        with self.db() as db:
            return [dict(x) for x in db.execute("SELECT models.*,backends.enabled AS backend_enabled FROM models JOIN backends ON backends.id=models.backend_id ORDER BY models.public_id")]

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
        caps = normalize(json.loads(key["capabilities"]))
        mods = json.loads(key["models"])
        return ("*" in caps or ROUTES.get(cap, cap) in caps) and (model is None or "*" in mods or model in mods)

    def discover(self, backend_id: str, model_ids: set[str]):
        with self.db() as db:
            db.execute("UPDATE models SET seen=0 WHERE backend_id=? AND manual=0", (backend_id,))
            for mid in sorted(model_ids):
                row = db.execute("SELECT 1 FROM models WHERE backend_id=? AND upstream_id=?", (backend_id, mid)).fetchone()
                if row:
                    db.execute("UPDATE models SET seen=1 WHERE backend_id=? AND upstream_id=?", (backend_id, mid))
                else:
                    public_id = mid
                    if db.execute("SELECT 1 FROM models WHERE public_id=?", (mid,)).fetchone():
                        public_id = f"{backend_id}::{mid}"
                    db.execute("INSERT INTO models(backend_id,upstream_id,public_id,published,seen,manual,endpoints) VALUES (?,?,?,?,1,0,?)", (backend_id, mid, public_id, 0, json.dumps([])))

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
