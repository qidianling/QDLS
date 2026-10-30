"""Runtime settings for QDLS.

Everything the desktop panel can change lives in ``settings.json`` next to the
kit root.  Paths are resolved once at import (changing them needs a gateway
restart); sound knobs are re-read for every job.
"""
from __future__ import annotations

import json
import os
import threading

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(KIT_ROOT, "settings.json")

DEFAULTS = {
    "language": "zh",
    "theme": "light",
    "voice": "taffy",
    "pitch_shift": 0,
    "cfg": 2.5,
    "n_steps": 40,
    "acappella": True,
    "denoise": {"enabled": True, "strength": 1.0},
    "peak": {"enabled": True, "ceiling_db": -3.0},
    "deess_db": -3.5,
    "consonant_mix": 0.25,
    "clip_min": 15,
    "clip_max": 28,
    "lift_max": 5,
    # ---- advanced: native SVC knobs + pipeline internals ----
    "auto_shift": False,
    "full_song": False,
    "seed": 42,
    "random_seed": False,
    "fp16": False,
    "lift_ratio": 0.78,
    "repair_jumps": True,
    "jump_semitones": 6,
    "gate_enabled": False,
    "gate_p95": 650,
    "gate_max": 750,
    "comp_thr": -26,
    "comp_ratio": 4.5,
    "paths": {
        "repo": "./SoulX-Singer",
        "pretrained": "./pretrained",
        "voices": "./voices",
        "work": "./work",
        "python": "python",
    },
    "gateway": {"host": "127.0.0.1", "port": 6767},
    "panel": {"host": "127.0.0.1", "port": 6768},
    "remote": {
        "enabled": False,
        "host": "",
        "user": "root",
        "key": "",
        "remote_port": 6767,
        "local_port": 6767,
        "container": "astrbot",
        "astrbot_config": "/opt/bots/astrbot/data/config/astrbot_plugin_matsuko_cover_config.json",
    },
}

_lock = threading.RLock()
_data = {}
_mtime = 0.0


def _refresh_if_changed() -> None:
    """Cheap stat check so a running process sees settings.json edits."""
    global _mtime
    try:
        m = os.path.getmtime(PATH)
    except OSError:
        return
    if m != _mtime:
        load()
        _mtime = m


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    global _data
    with _lock:
        raw = {}
        if os.path.exists(PATH):
            try:
                raw = json.load(open(PATH, encoding="utf-8-sig"))
            except Exception:
                raw = {}
        _data = _merge(DEFAULTS, raw)
        return _data


def reload() -> dict:
    return load()


def get(path: str, default=None):
    if not _data:
        load()
    else:
        _refresh_if_changed()
    node = _data
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def save(data: dict) -> dict:
    global _data
    with _lock:
        _data = _merge(DEFAULTS, data or {})
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
        return _data


def update(**kwargs) -> dict:
    with _lock:
        data = dict(_data or load())
        for k, v in kwargs.items():
            data[k] = v
        return save(data)


def resolve(path: str) -> str:
    """Resolve a possibly relative path against the kit root."""
    if not path:
        return KIT_ROOT
    p = os.path.expanduser(str(path))
    if not os.path.isabs(p):
        p = os.path.join(KIT_ROOT, p)
    return os.path.normpath(p)


load()

