"""
Redis cache client — singleton following the same pattern as neo4j_client.py.

All Redis calls are wrapped in try/except so the app degrades gracefully
when Redis is unavailable: Neo4j queries run normally, results are just
not cached.
"""

import hashlib
import json
import os

import redis as redis_lib

_client = None


def get_client() -> redis_lib.Redis:
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _client


def close():
    global _client
    if _client is not None:
        _client.close()
        _client = None


def make_key(namespace: str, parts: list[str]) -> str:
    """
    Build a deterministic cache key.

    Parts are sorted so key("interactions", ["aspirin", "ibuprofen"]) ==
    key("interactions", ["ibuprofen", "aspirin"]).  A short SHA-256 prefix
    keeps keys compact while avoiding collisions.
    """
    payload = "|".join(sorted(parts))
    h = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{namespace}:v1:{h}"


def cache_get(key: str):
    try:
        val = get_client().get(key)
        return json.loads(val) if val else None
    except Exception:
        return None  # Redis unavailable — degrade gracefully


def cache_set(key: str, value, ttl: int = 30 * 86400):
    try:
        get_client().setex(key, ttl, json.dumps(value))
    except Exception:
        pass  # Redis unavailable — degrade gracefully


def flush_interaction_cache() -> int:
    """
    Delete all cached interaction and class-fallback entries.

    Called by the ingest script after new data is written to Neo4j, so stale
    cached results are never served.  Uses SCAN+DEL by prefix rather than
    FLUSHALL to avoid clearing unrelated keys if Redis is ever shared.

    Returns the number of keys deleted (0 if Redis is unavailable).
    """
    try:
        client = get_client()
        deleted = 0
        for prefix in ("interactions:v1:*", "class-fallback:v1:*"):
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor, match=prefix, count=100)
                if keys:
                    client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
        return deleted
    except Exception:
        return 0
