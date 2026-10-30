"""Job queue + REST API for QDLS (used by the panel and by AstrBot plugins)."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
import urllib.parse

import access
import settings

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = None
GW_PORT = 6767
API_PORT = 6768


def init(work_dir: str, gw_port: int, api_port: int = 6768) -> None:
    global WORK, GW_PORT, API_PORT
    WORK = work_dir
    GW_PORT = gw_port
    API_PORT = api_port
    os.makedirs(WORK, exist_ok=True)


_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_queue: list[str] = []
_history: list[dict] = []
_latest = {"id": None}
_worker_started = False


def _now() -> float:
    return time.time()


def enqueue(song: str, voice: str | None = None, requester: str = "", source: str = "panel") -> dict:
    jid = "j" + secrets.token_hex(5)
    job = {
        "id": jid, "song": song, "voice": voice or settings.get("voice", "taffy"),
        "requester": requester[:60], "source": source,
        "state": "queued", "stage": "排队中", "error": "",
        "created": _now(), "started": 0.0, "finished": 0.0, "file": None,
    }
    with _lock:
        _jobs[jid] = job
        _queue.append(jid)
        _latest["id"] = jid
    _ensure_worker()
    return job


def _ensure_worker() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker, daemon=True).start()


def _worker() -> None:
    while True:
        with _lock:
            jid = _queue.pop(0) if _queue else None
        if not jid:
            time.sleep(0.4)
            continue
        job = _jobs.get(jid)
        if not job:
            continue
        try:
            _run(job)
        except Exception as exc:  # keep the worker alive
            job["state"] = "error"
            job["error"] = str(exc)[:200]
            job["finished"] = _now()


def _run(job: dict) -> None:
    from gradio_client import Client

    job["state"] = "running"
    job["started"] = _now()
    job["stage"] = "连接网关"
    c = Client(f"http://127.0.0.1:{GW_PORT}/", verbose=False, analytics_enabled=False)
    job["stage"] = "推理中"
    out = c.predict(
        song_name_src=job["song"], model_dropdown=job["voice"],
        pitch_shift="", n_step="", cfg="",
        auto_shift=bool(settings.get("auto_shift", False)),
        auto_mix_acc=None,
        seed=int(settings.get("seed", 42) or 42),
        random_seed=bool(settings.get("random_seed", False)),
        prompt_vocal_sep=True, target_vocal_sep=True, target_separation="none",
        msst_batch_size=1, msst_num_overlap=4, msst_normalize=False,
        msst_use_tta=False, msst_model="",
        full_song=bool(settings.get("full_song", False)), api_name="/convert",
    )
    path = out[0] if isinstance(out, (list, tuple)) else out
    dst = os.path.join(WORK, f"{job['id']}.mp3")
    shutil.copyfile(str(path), dst)
    job["file"] = dst
    job["state"] = "done"
    job["stage"] = "完成"
    job["finished"] = _now()
    with _lock:
        _history.insert(0, {"id": job["id"], "song": job["song"], "voice": job["voice"],
                            "file": dst, "time": time.strftime("%H:%M:%S"),
                            "source": job.get("source", "panel")})
        del _history[20:]


# ------------------------------------------------------------------ reads
def get(jid: str) -> dict | None:
    with _lock:
        return dict(_jobs[jid]) if jid in _jobs else None


def latest() -> dict | None:
    with _lock:
        jid = _latest.get("id")
        return dict(_jobs[jid]) if jid and jid in _jobs else None


def history(limit: int = 12) -> list[dict]:
    with _lock:
        return [{"i": i, "id": h["id"], "song": h["song"], "time": h["time"],
                 "source": h.get("source", "")} for i, h in enumerate(_history[:limit])]


def history_file(idx: int) -> str | None:
    with _lock:
        if 0 <= idx < len(_history):
            return _history[idx]["file"]
    return None


def cancel(jid: str) -> bool:
    with _lock:
        job = _jobs.get(jid)
        if not job or job["state"] in ("done", "error", "cancelled"):
            return False
        if jid in _queue:
            _queue.remove(jid)
            job["state"] = "cancelled"
            return True
    return False


def queue_len() -> int:
    with _lock:
        return len(_queue)


# ------------------------------------------------------------------ REST
def _json(obj, code=200):
    return code, obj


def api_handle(path: str, method: str, payload: dict, token: str,
               query: dict) -> tuple[int, dict] | None:
    """Return (status, body) for /api/v1/* or None if not an API route."""
    if not path.startswith("/api/v1/"):
        return None
    route = path[len("/api/v1/"):].strip("/")

    if route == "pair/claim" and method == "POST":
        res = access.claim(str(payload.get("code") or ""),
                           str(payload.get("bot_name") or ""),
                           str(payload.get("platform") or ""))
        if not res.get("ok"):
            return _json(res, 403)
        base = f"http://127.0.0.1:{API_PORT}"
        return _json({"ok": True, "token": res["token"], "id": res["id"],
                      "gateway": base, "api": base})

    entry = access.verify(token)
    if not entry:
        return _json({"ok": False, "error": "invalid token"}, 401)

    if route == "health":
        return _json({"ok": True, "queue": queue_len(),
                      "busy": bool(latest() and latest()["state"] == "running"),
                      "voices": [p["id"] for p in _voices()]})

    if route == "voices":
        return _json({"ok": True, "voices": _voices()})

    if route == "register" and method == "POST":
        access.touch_bot(entry, str(payload.get("bot_name") or ""),
                         str(payload.get("platform") or ""))
        return _json({"ok": True})

    if route == "sing" and method == "POST":
        song = str(payload.get("song") or "").strip()
        if not song:
            return _json({"ok": False, "error": "empty song"}, 400)
        job = enqueue(song, payload.get("voice"), str(payload.get("requester") or ""), "astrbot")
        return _json({"ok": True, "job_id": job["id"]})

    if route.startswith("jobs/"):
        parts = route.split("/")
        jid = parts[1] if len(parts) > 1 else ""
        action = parts[2] if len(parts) > 2 else ""
        job = get(jid)
        if not job:
            return _json({"ok": False, "error": "job not found"}, 404)
        if action == "cancel" and method == "POST":
            return _json({"ok": cancel(jid)})
        if action == "audio":
            f = job.get("file")
            if not f or not os.path.exists(f):
                return _json({"ok": False, "error": "not ready"}, 409)
            return _json({"ok": True, "path": f})
        elapsed = (job["finished"] or _now()) - (job["started"] or job["created"])
        return _json({"ok": True, "job": {
            "id": job["id"], "state": job["state"], "stage": job["stage"],
            "song": job["song"], "voice": job["voice"], "error": job["error"],
            "elapsed": round(elapsed, 1)}})

    return _json({"ok": False, "error": "unknown route"}, 404)


def _voices() -> list[dict]:
    import engine
    try:
        return [{"id": p["profile_id"], "name": p.get("display_name") or p["profile_id"]}
                for p in engine.list_profiles()]
    except Exception:
        return []
