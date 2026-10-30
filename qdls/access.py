"""Pairing codes, API tokens and connected-bot registry for QDLS."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(KIT_ROOT, "access.json")

_lock = threading.RLock()
_data = {"tokens": [], "codes": [], "bots": []}


def _load() -> dict:
    global _data
    if os.path.exists(PATH):
        try:
            d = json.load(open(PATH, encoding="utf-8-sig"))
        except Exception:
            d = {}
    else:
        d = {}
    for key in ("tokens", "codes", "bots"):
        d.setdefault(key, [])
    _data = d
    return _data


def _save() -> None:
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def all_state() -> dict:
    with _lock:
        _load()
        now = time.time()
        _data["codes"] = [c for c in _data["codes"] if c.get("expires", 0) > now]
        safe = json.loads(json.dumps(_data))
        for t in safe.get("tokens", []):
            t.pop("hash", None)
        return safe


def new_code(minutes: int = 10) -> dict:
    """32-char URL-safe one-time pairing code (~192 bits of entropy)."""
    with _lock:
        _load()
        code = secrets.token_urlsafe(24)  # 24 bytes -> 32 url-safe chars
        entry = {"code": code, "expires": time.time() + minutes * 60,
                 "created": time.time(), "used": False}
        _data["codes"] = [c for c in _data["codes"] if c.get("expires", 0) > time.time()]
        _data["codes"].append(entry)
        _save()
        return {"code": code, "expires_in": minutes * 60}


_attempts = {"count": 0, "window": 0.0}


def claim(code: str, bot_name: str = "", platform: str = "") -> dict:
    """Exchange a one-time code for a long-lived token (rate limited)."""
    with _lock:
        now0 = time.time()
        if now0 - _attempts["window"] > 60:
            _attempts["window"] = now0
            _attempts["count"] = 0
        _attempts["count"] += 1
        if _attempts["count"] > 10:
            return {"ok": False, "error": "尝试过于频繁，请稍后再试"}
        _load()
        now = time.time()
        code = str(code or "").strip()
        if len(code) < 16:
            return {"ok": False, "error": "配对码格式不正确"}
        found = None
        for c in _data["codes"]:
            if hmac.compare_digest(c.get("code", ""), code) and not c.get("used") \
                    and c.get("expires", 0) > now:
                found = c
                break
        if not found:
            return {"ok": False, "error": "配对码无效或已过期"}
        found["used"] = True
        token = "qdls_" + secrets.token_urlsafe(32)
        entry = {
            "id": "tk_" + secrets.token_hex(4),
            "name": (bot_name or "AstrBot").strip()[:40],
            "platform": (platform or "").strip()[:40],
            "hash": _hash(token),
            "created": now,
            "last_seen": now,
            "calls": 0,
        }
        _data["tokens"].append(entry)
        _data["bots"] = [b for b in _data["bots"] if b.get("token_id") != entry["id"]]
        _data["bots"].append({"token_id": entry["id"], "name": entry["name"],
                              "platform": entry["platform"], "seen": now})
        _save()
        return {"ok": True, "token": token, "id": entry["id"], "name": entry["name"]}


def issue_token(name: str = "", platform: str = "") -> dict:
    """Create a token directly (manual setup: user copies it into the client)."""
    with _lock:
        _load()
        now = time.time()
        token = "qdls_" + secrets.token_urlsafe(32)
        entry = {
            "id": "tk_" + secrets.token_hex(4),
            "name": (name or "manual").strip()[:40],
            "platform": (platform or "").strip()[:40],
            "hash": _hash(token),
            "created": now,
            "last_seen": now,
            "calls": 0,
        }
        _data["tokens"].append(entry)
        _data["bots"] = [b for b in _data["bots"] if b.get("token_id") != entry["id"]]
        _data["bots"].append({"token_id": entry["id"], "name": entry["name"],
                              "platform": entry["platform"], "seen": now})
        _save()
        return {"ok": True, "token": token, "id": entry["id"], "name": entry["name"]}


def verify(token: str) -> dict | None:
    if not token:
        return None
    h = _hash(token)
    with _lock:
        _load()
        for t in _data["tokens"]:
            if hmac.compare_digest(t.get("hash", ""), h):
                t["last_seen"] = time.time()
                t["calls"] = int(t.get("calls", 0)) + 1
                for b in _data["bots"]:
                    if b.get("token_id") == t["id"]:
                        b["seen"] = time.time()
                try:
                    _save()
                except Exception:
                    pass
                return t
    return None


def revoke(token_id: str) -> bool:
    with _lock:
        _load()
        before = len(_data["tokens"])
        _data["tokens"] = [t for t in _data["tokens"] if t.get("id") != token_id]
        _data["bots"] = [b for b in _data["bots"] if b.get("token_id") != token_id]
        _save()
        return len(_data["tokens"]) != before


def touch_bot(token_entry: dict, bot_name: str, platform: str) -> None:
    with _lock:
        _load()
        for b in _data["bots"]:
            if b.get("token_id") == token_entry.get("id"):
                b["name"] = (bot_name or b.get("name"))[:40]
                b["platform"] = (platform or b.get("platform") or "")[:40]
                b["seen"] = time.time()
                _save()
                return
        _data["bots"].append({"token_id": token_entry.get("id"),
                              "name": (bot_name or "AstrBot")[:40],
                              "platform": (platform or "")[:40], "seen": time.time()})
        _save()
