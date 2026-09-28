"""Durable API-key storage for BRAIN, backed by Render Postgres."""

import hashlib
import os
import secrets

import psycopg


class KeyStoreNotConfigured(RuntimeError):
    pass


def _database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise KeyStoreNotConfigured(
            "DATABASE_URL is missing. Create and link a Render Postgres database before using API keys."
        )
    return url


def _connect():
    return psycopg.connect(_database_url())


def initialize():
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS brain_api_keys (
                    id BIGSERIAL PRIMARY KEY,
                    key_hash TEXT UNIQUE NOT NULL,
                    key_prefix TEXT NOT NULL,
                    label TEXT NOT NULL,
                    revoked BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_used_at TIMESTAMPTZ
                )
            """)


def create_key(label):
    label = (label or "").strip()
    if not label:
        raise ValueError("`label` is required")
    if len(label) > 100:
        raise ValueError("`label` must be 100 characters or fewer")
    initialize()
    secret = "brain_live_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(secret.encode()).hexdigest()
    prefix = secret[:19]
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO brain_api_keys (key_hash, key_prefix, label) VALUES (%s, %s, %s) RETURNING id, created_at",
                (digest, prefix, label),
            )
            key_id, created_at = cursor.fetchone()
    return {"id": key_id, "key": secret, "prefix": prefix, "label": label, "created_at": created_at.isoformat()}


def list_keys():
    initialize()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, key_prefix, label, revoked, created_at, last_used_at
                FROM brain_api_keys ORDER BY id DESC
            """)
            rows = cursor.fetchall()
    return [
        {"id": row[0], "prefix": row[1], "label": row[2], "revoked": row[3],
         "created_at": row[4].isoformat(), "last_used_at": row[5].isoformat() if row[5] else None}
        for row in rows
    ]


def revoke_key(key_id):
    initialize()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE brain_api_keys SET revoked = TRUE WHERE id = %s", (key_id,))
            if cursor.rowcount == 0:
                raise ValueError("API key not found")


def validate_key(secret):
    if not secret:
        return False
    initialize()
    digest = hashlib.sha256(secret.encode()).hexdigest()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE brain_api_keys SET last_used_at = NOW()
                WHERE key_hash = %s AND revoked = FALSE
                RETURNING id
            """, (digest,))
            return cursor.fetchone() is not None
