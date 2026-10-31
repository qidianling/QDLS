"""QDLS desktop panel.

A single local process that owns the GPU gateway (and, optionally, the reverse
tunnel to a cloud AstrBot).  The UI is one page served locally and opened in an
app-mode window, so no extra consoles or shortcuts are involved.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_ROOT = os.path.dirname(HERE)
sys.path.insert(0, KIT_ROOT)
sys.path.insert(0, HERE)

import settings  # noqa: E402
import access  # noqa: E402
import jobs  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008

STATE = os.path.join(KIT_ROOT, "panel_state.json")
LOG = os.path.join(KIT_ROOT, "gateway.log")
WORK = settings.resolve(settings.get("paths.work", "./work"))
VOICES = settings.resolve(settings.get("paths.voices", "./voices"))
UPLOADS = os.path.join(KIT_ROOT, "uploads")

GW_PORT = int(settings.get("gateway.port", 6767))
PANEL_PORT = int(settings.get("panel.port", 6768))

_lock = threading.RLock()
_state = {"gateway_pid": None, "tunnel_pid": None, "desired": True}
if os.path.exists(STATE):
    try:
        _state.update(json.load(open(STATE, encoding="utf-8")))
    except Exception:
        pass

_cache = {"remote": (0, None)}
_test = {"state": "idle", "msg": "", "file": None}
_build = {"state": "idle", "msg": "", "id": None}
_song = {"state": "idle", "msg": "", "file": None, "song": ""}
_history = []


# ---------------------------------------------------------------- utilities
def save_state():
    try:
        json.dump(_state, open(STATE, "w", encoding="utf-8"))
    except Exception:
        pass


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def kill_pid(pid):
    if not pid:
        return
    try:
        import psutil
        p = psutil.Process(int(pid))
        for child in p.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
        p.kill()
    except Exception:
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)


def python_exe() -> str:
    return settings.get("paths.python") or sys.executable


def log_event(msg: str) -> None:
    try:
        with open(os.path.join(KIT_ROOT, "panel.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- services
def start_gateway():
    _state["desired"] = True
    if port_open(GW_PORT):
        return
    log_event("starting gateway")
    subprocess.Popen(
        [python_exe(), "-u", os.path.join(HERE, "gateway.py"), "--port", str(GW_PORT)],
        cwd=KIT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW | DETACHED)


def _remote_ssh_base() -> list:
    remote = settings.get("remote") or {}
    key = settings.resolve(remote.get("key"))
    return ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=" + os.path.join(KIT_ROOT, "known_hosts"),
            "-o", "ConnectTimeout=12", "-o", "LogLevel=ERROR",
            "{0}@{1}".format(remote.get("user") or "root", remote.get("host"))]


def reset_remote_ports() -> dict:
    """Kill sshd sessions holding the forwarded ports, so a fresh tunnel can bind."""
    remote = settings.get("remote") or {}
    if not remote.get("enabled") or not remote.get("host"):
        return {"ok": False, "error": "未启用云端接入"}
    ports = remote.get("ports") or [int(remote.get("remote_port") or GW_PORT), PANEL_PORT]
    plist = " ".join(str(int(p)) for p in ports)
    checks = " ".join("127.0.0.1:" + str(int(p)) for p in ports)
    sh = (
        "for p in " + plist + "; do "
        "pid=$(ss -lntp 2>/dev/null | grep \"127.0.0.1:$p \" | grep -o 'pid=[0-9]*' "
        "| head -1 | cut -d= -f2); "
        "if [ -n \"$pid\" ]; then echo kill:$p:$pid; kill -9 $pid 2>/dev/null; fi; done; "
        "sleep 1; "
        "left=$(ss -lnt | grep -E '(" + checks.replace(" ", "|") + ")'); "
        "if [ -z \"$left\" ]; then echo free; else echo \"still:\" $left; fi"
    )
    try:
        r = subprocess.run(_remote_ssh_base() + [sh], capture_output=True, text=True,
                           timeout=30, creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or "") + (r.stderr or "")
        killed = [ln for ln in out.splitlines() if ln.startswith("kill:")]
        free = "free" in out.splitlines()
        log_event("reset remote ports: {0} killed, free={1}".format(len(killed), free))
        return {"ok": True, "killed": killed, "free": free, "raw": out.strip()[:400]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def start_tunnel():
    remote = settings.get("remote", {}) or {}
    if not remote.get("enabled") or not remote.get("host") or not remote.get("key"):
        return
    if pid_alive(_state.get("tunnel_pid")):
        return
    key = settings.resolve(remote.get("key"))
    if not os.path.exists(key):
        log_event(f"tunnel key missing: {key}")
        return
    rport = int(remote.get("remote_port") or GW_PORT)
    lport = int(remote.get("local_port") or GW_PORT)
    # the API lives on the panel port; forward it too so remote bots can pair
    ports = remote.get("ports") or [rport, PANEL_PORT]
    args = ["ssh", "-N", "-i", key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=" + os.path.join(KIT_ROOT, "known_hosts"),
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10",
            "-o", "ServerAliveCountMax=3", "-o", "TCPKeepAlive=yes", "-o", "ConnectTimeout=15"]
    for prt in ports:
        prt = int(prt)
        local = lport if prt == rport else prt
        args += ["-R", f"127.0.0.1:{prt}:127.0.0.1:{local}"]
    args.append(f"{remote.get('user') or 'root'}@{remote.get('host')}")
    log_event(f"starting tunnel ports={ports}")
    p = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | DETACHED)
    _state["tunnel_pid"] = p.pid
    save_state()


def stop_all():
    kill_pid(_state.get("tunnel_pid"))
    kill_pid(_state.get("gateway_pid"))
    _state.update({"gateway_pid": None, "tunnel_pid": None, "desired": False})
    save_state()
    # gateway may have been started outside the panel
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == GW_PORT and conn.pid:
                kill_pid(conn.pid)
    except Exception:
        pass
    time.sleep(0.5)
    return {"ok": not port_open(GW_PORT)}


def ssh_script(script: str, timeout=20, python=False) -> str:
    remote = settings.get("remote", {}) or {}
    if not remote.get("host"):
        return ""
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    tail = "python3 -" if python else "bash"
    try:
        out = subprocess.run(
            ["ssh", "-i", settings.resolve(remote.get("key")),
             "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=" + os.path.join(KIT_ROOT, "known_hosts"),
             "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
             f"{remote.get('user') or 'root'}@{remote.get('host')}",
             f"echo {b64} | base64 -d | {tail}"],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def remote_status():
    remote = settings.get("remote", {}) or {}
    if not remote.get("enabled") or not remote.get("host"):
        return "off"
    now = time.time()
    if now - _cache["remote"][0] < 8:
        return _cache["remote"][1]
    rport, cname = int(remote.get("remote_port") or GW_PORT), remote.get("container") or "astrbot"
    cmd = (f"docker exec {cname} curl -s -m 3 -o /dev/null -w '%{{http_code}}' "
           f"http://172.17.0.1:{rport}/config")
    code = ssh_script(cmd).strip() or "000"
    _cache["remote"] = (now, code)
    return code


# ---------------------------------------------------------------- state
STAGE_FRACTION = [
    ("prepare", 0.03),
    ("separate", 0.14),
    ("clip length", 0.16),
    ("excerpt", 0.17),
    ("extract", 0.22),
    ("zero-shot", 0.60),
    ("svc done", 0.80),
    ("repaired", 0.86),
    ("polished", 0.93),
    ("mix and export", 0.97),
    ("done", 1.0),
]

_eta_state = {"ema": 90.0, "job": None, "elapsed": 0.0}


def stage_fraction(stage: str) -> float:
    s = (stage or "").lower()
    for key, frac in STAGE_FRACTION:
        if key in s:
            return frac
    return 0.10


def note_job_finished(job: str, duration: float) -> None:
    """Track how long a real job took so the estimate converges to this machine."""
    if job != _eta_state["job"] or duration < 8:
        return
    _eta_state["ema"] = _eta_state["ema"] * 0.6 + float(duration) * 0.4
    _eta_state["job"] = None
    _eta_state["elapsed"] = 0.0


def job_eta(jb: dict) -> int:
    """Remaining seconds: history-based total minus elapsed, blended with stage."""
    if not jb.get("busy"):
        return 0
    elapsed = float(jb.get("elapsed") or 0)
    if elapsed < 1:
        return int(max(5, _eta_state["ema"]))
    frac = min(max(stage_fraction(jb.get("stage")), 0.03), 0.97)
    by_stage = elapsed * (1.0 - frac) / frac
    by_history = max(3.0, _eta_state["ema"] - elapsed)
    remaining = 0.6 * by_history + 0.4 * by_stage if by_stage > 0 else by_history
    return int(max(3, min(remaining, 7200)))


def _validate_settings(payload: dict) -> str:
    """Refuse settings that would let a rogue request execute arbitrary code."""
    paths = payload.get("paths") or {}
    if isinstance(paths, dict):
        py = str(paths.get("python") or "").strip()
        if py:
            real = settings.resolve(py)
            base = os.path.basename(real).lower()
            if not base.startswith("python") or not base.endswith(".exe") or not os.path.isfile(real):
                return "python 路径无效（必须是 python*.exe）"
        for key in ("repo", "pretrained", "voices", "work"):
            v = str(paths.get(key) or "").strip()
            if not v or v.startswith("./"):
                continue
            real = settings.resolve(v)
            if not os.path.isdir(real):
                return f"{key} 目录不存在"
            if key == "repo" and not os.path.isdir(os.path.join(real, "preprocess", "tools")):
                return "repo 不是有效的 SoulX-Singer 目录"
            if key == "pretrained" and not os.path.exists(os.path.join(real, "model-svc.pt")) \
                    and os.listdir(real):
                return "pretrained 里没有 model-svc.pt"
    for key in ("pitch_shift", "cfg", "n_steps", "seed", "clip_min", "clip_max", "lift_max"):
        if key in payload:
            try:
                int(float(payload[key]))
            except Exception:
                return f"{key} 必须是数字"
    return ""


def job_state():
    path = os.path.join(WORK, "job_state.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"busy": False, "stage": "idle", "elapsed": 0, "job": "", "error": "", "eta": 0}
    stage = d.get("stage") or ""
    age = time.time() - float(d.get("updated") or 0)
    job = d.get("job") or ""
    if stage.startswith("error"):
        return {"busy": False, "stage": "error", "elapsed": 0,
                "job": job, "error": stage[6:].strip(), "eta": 0}
    busy = stage not in ("idle", "") and age < 600
    elapsed = time.time() - float(d.get("started") or time.time()) if busy else 0
    if busy:
        _eta_state["job"] = job
        _eta_state["elapsed"] = elapsed
    elif _eta_state["job"] == job and job:
        note_job_finished(job, float(d.get("updated") or time.time()) - float(d.get("started") or 0))
    out = {"busy": busy, "stage": stage or "idle", "elapsed": round(elapsed, 1),
           "job": job, "error": ""}
    out["eta"] = job_eta(out)
    return out


def hw_stats():
    data = {"gpu": None, "cpu": None, "ram": None, "gw_rss": None}
    try:
        out = subprocess.run(
            [r"C:\Windows\System32\nvidia-smi.exe",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6, creationflags=CREATE_NO_WINDOW)
        parts = (out.stdout or "").strip().splitlines()[0].split(",")
        data["gpu"] = {"util": float(parts[0]), "mem": float(parts[1]),
                       "mem_total": float(parts[2]), "temp": float(parts[3]),
                       "power": float(parts[4]) if parts[4].strip() != "[N/A]" else None,
                       "name": parts[5].strip() if len(parts) > 5 else ""}
    except Exception:
        pass
    try:
        import psutil
        data["cpu"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        data["ram"] = {"used": round(vm.used / 1e9, 1), "total": round(vm.total / 1e9, 1),
                       "percent": vm.percent}
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == GW_PORT and conn.pid:
                    data["gw_rss"] = round(psutil.Process(conn.pid).memory_info().rss / 1e9, 2)
                    break
        except Exception:
            pass
    except Exception:
        pass
    return data


def _tail_file(path: str, n: int) -> list:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
        return lines[-n:]
    except Exception:
        return []


def tail_log(n: int = 60) -> list:
    """Merge the engine log and the panel log, ordered by their HH:MM:SS stamp."""
    lines = _tail_file(os.path.join(HERE, "gateway.log"), 200)
    lines += _tail_file(os.path.join(KIT_ROOT, "panel.log"), 80)
    if not lines:
        return []

    def key(line: str) -> str:
        m = re.match(r"\[(\d\d:\d\d:\d\d)\]", line)
        return m.group(1) if m else "99:99:99"

    lines.sort(key=key)
    return lines[-n:]


def profiles():
    out = []
    try:
        for name in sorted(os.listdir(VOICES)):
            if name.startswith("_") or name.startswith("."):
                continue
            ref = os.path.join(VOICES, name, "ref.wav")
            if os.path.isfile(ref):
                meta = {}
                mp = os.path.join(VOICES, name, "meta.json")
                if os.path.exists(mp):
                    try:
                        meta = json.load(open(mp, encoding="utf-8"))
                    except Exception:
                        pass
                out.append({"id": name, "name": meta.get("display_name") or name})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- actions
def run_song(song: str, voice_id: str):
    """Song-request console: same path the bot uses, but driven from the panel."""
    try:
        jobs.enqueue(song, voice_id, source="panel")
    except Exception as exc:
        raise RuntimeError(str(exc))


def run_test(voice_id):
    global _test
    _test = {"state": "running", "msg": "preparing", "file": None}
    try:
        from gradio_client import Client, handle_file
        src = _pick_test_source()
        if not src:
            raise RuntimeError("no test audio available")
        c = Client(f"http://127.0.0.1:{GW_PORT}/", verbose=False, analytics_enabled=False)
        _test["msg"] = "uploading"
        staged = c.predict(file=handle_file(src), api_name="/upload_audio")
        _test["msg"] = "inference"
        out = c.predict(
            song_name_src=staged, model_dropdown=voice_id or "", pitch_shift="", n_step="",
            cfg="",
            auto_shift=bool(settings.get("auto_shift", False)), auto_mix_acc=None,
            seed=int(settings.get("seed", 42) or 42),
            random_seed=bool(settings.get("random_seed", False)),
            prompt_vocal_sep=True, target_vocal_sep=True, target_separation="none",
            msst_batch_size=1, msst_num_overlap=4, msst_normalize=False,
            msst_use_tta=False, msst_model="",
            full_song=bool(settings.get("full_song", False)), api_name="/convert")
        path = out[0] if isinstance(out, (list, tuple)) else out
        dst = os.path.join(WORK, "panel_test_out.mp3")
        import shutil
        shutil.copyfile(str(path), dst)
        _test = {"state": "done", "msg": "done", "file": dst}
    except Exception as exc:
        _test = {"state": "error", "msg": str(exc)[:200], "file": None}


def _pick_test_source():
    os.makedirs(UPLOADS, exist_ok=True)
    dst = os.path.join(UPLOADS, "panel_test.mp3")
    if os.path.exists(dst) and os.path.getsize(dst) > 100_000:
        return dst
    for c in (os.path.join(KIT_ROOT, "samples", "demo.mp3"),
              os.path.join(KIT_ROOT, "samples", "demo.m4a")):
        if os.path.exists(c):
            try:
                import shutil
                shutil.copyfile(c, dst)
                return dst
            except Exception:
                pass
    return None


def build_voice_profile(name: str, raw: bytes, filename: str):
    global _build
    _build = {"state": "running", "msg": "uploading", "id": None}
    try:
        os.makedirs(UPLOADS, exist_ok=True)
        safe = os.path.basename(filename or "reference.wav")
        local = os.path.join(UPLOADS, f"{int(time.time())}_{safe}")
        with open(local, "wb") as f:
            f.write(raw)
        from gradio_client import Client, handle_file
        c = Client(f"http://127.0.0.1:{GW_PORT}/", verbose=False, analytics_enabled=False)
        _build["msg"] = "separating / selecting 20s"
        out = c.predict(file=handle_file(local), name=name or "", api_name="/build_voice")
        data = json.loads(out) if isinstance(out, str) else (out or {})
        if data.get("ok"):
            _build = {"state": "done", "msg": "ok", "id": data.get("id")}
        else:
            _build = {"state": "error", "msg": str(data.get("error"))[:200], "id": None}
    except Exception as exc:
        _build = {"state": "error", "msg": str(exc)[:200], "id": None}


GUARD = {"strikes": 0, "fired": 0.0}


def guard_check() -> dict:
    """Watch system RAM while idle; alert and self-terminate before the OS OOMs."""
    cfg = settings.get("guard") or {}
    if not cfg.get("enabled", True):
        return {"enabled": False}
    threshold = float(cfg.get("ram_percent", 90) or 90)
    need = int(cfg.get("idle_seconds", 60) or 60)
    try:
        import psutil
        pct = float(psutil.virtual_memory().percent)
    except Exception:
        return {"enabled": True, "ram_percent": 0.0, "armed": False}
    busy = bool(job_state().get("busy"))
    if busy or pct < threshold:
        GUARD["strikes"] = 0
        return {"enabled": True, "ram_percent": pct, "armed": False}
    GUARD["strikes"] += 1
    if GUARD["strikes"] * 10 < need:
        return {"enabled": True, "ram_percent": pct, "armed": False,
                "strikes": GUARD["strikes"]}
    GUARD["strikes"] = 0
    GUARD["fired"] = time.time()
    log_event("guard: RAM %.0f%% >= %.0f%% while idle, showing alert and stopping" % (pct, threshold))
    threading.Thread(target=_alert_and_quit,
                     args=(pct, threshold), daemon=True).start()
    return {"enabled": True, "ram_percent": pct, "armed": True, "fired": True}


def _alert_and_quit(pct: float, threshold: float) -> None:
    msg = ("QDLS 检测到系统内存占用 %.0f%%（阈值 %.0f%%），继续运行可能触发内存耗尽。\n\n"
           "已自动停止翻唱服务以释放内存，请关闭部分程序后再启动。") % (pct, threshold)
    try:
        script = os.path.join(KIT_ROOT, "tools", "oom_alert.ps1")
        if os.path.exists(script):
            subprocess.Popen(["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                              "-ExecutionPolicy", "Bypass", "-File", script, "-Message", msg],
                             creationflags=CREATE_NO_WINDOW)
    except Exception as exc:
        log_event("guard: alert failed %s" % exc)
    time.sleep(2)
    stop_all()
    time.sleep(1)
    os._exit(0)


def watchdog():
    fails = 0
    while True:
        time.sleep(10)
        try:
            guard_check()
            with _lock:
                if not _state.get("desired", True):
                    continue
                if not port_open(GW_PORT):
                    start_gateway()
                if settings.get("remote.enabled"):
                    if not pid_alive(_state.get("tunnel_pid")):
                        start_tunnel()
                        fails = 0
                        continue
                    if job_state().get("busy"):
                        continue
                    if remote_status() == "200":
                        fails = 0
                    else:
                        fails += 1
                        if fails >= 3:
                            fails = 0
                            kill_pid(_state.get("tunnel_pid"))
                            _state["tunnel_pid"] = None
                            save_state()
                            start_tunnel()
        except Exception:
            pass


# ---------------------------------------------------------------- html
HTML = open(os.path.join(HERE, "ui.html"), encoding="utf-8").read()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _origin_ok(self) -> bool:
        """Block cross-site requests to the local API (CSRF / DNS rebinding)."""
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin:
            allowed = {f"http://127.0.0.1:{PANEL_PORT}", f"http://localhost:{PANEL_PORT}"}
            if origin not in allowed:
                return False
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return False
        return True

    def _json(self, obj, code=200):
        return self._send(code, json.dumps(obj, ensure_ascii=False))

    def _api_route(self, method: str, path: str, payload: dict) -> bool:
        """Write the response for /api/v1/* and /api/access/*; True if handled."""
        res = self._api_dispatch(method, path, payload)
        if res is None:
            return False
        status, body = res
        if isinstance(body, dict) and body.get("path") and os.path.exists(body["path"]):
            import re as _re
            from urllib.parse import quote
            data = open(body["path"], "rb").read()
            fname = _re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(body["path"])) or "cover.mp3"
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Disposition",
                             f"attachment; filename=cover.mp3; filename*=UTF-8''{quote(fname)}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True
        self._json(body, status)
        return True

    def _api_dispatch(self, method: str, path: str, payload: dict):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        token = ""
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            token = (q.get("token") or [""])[0]
            if token and not path.endswith("/audio"):
                token = ""

        if path.startswith("/api/access"):
            route = path[len("/api/access/"):].strip("/")
            if route == "state":
                return 200, access.all_state()
            if route == "code":
                return 200, access.new_code()
            if route == "revoke":
                return 200, {"ok": access.revoke(str(payload.get("id") or ""))}
            if route == "token":
                return 200, access.issue_token(str(payload.get("name") or ""),
                                               str(payload.get("platform") or ""))
            if route == "dsh_credential":
                res = access.issue_token("DeepSeek Harness", "dsh")
                if not res.get("ok"):
                    return 500, res
                candidates = [
                    os.path.join(KIT_ROOT, "qdls.token"),
                    os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                                 "QDLS", "dsh.token"),
                ]
                saved = ""
                for target in candidates:
                    try:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with open(target, "w", encoding="utf-8") as f:
                            f.write(res["token"] + "\n")
                        saved = target
                        break
                    except Exception:
                        continue
                if not saved:
                    return 500, {"ok": False, "error": "无法写入凭据文件"}
                return 200, {"ok": True, "id": res["id"], "path": saved,
                             "token_tail": res["token"][-6:]}
            return 404, {"ok": False, "error": "unknown"}

        return jobs.api_handle(path, method, payload, token,
                               {k: v[0] for k, v in q.items()})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/") and not self._origin_ok():
            return self._json({"ok": False, "error": "cross-origin blocked"}, 403)
        if path.startswith("/api/v1/") or path.startswith("/api/access"):
            if self._api_route("GET", path, {}):
                return
        if path in ("/icon.png", "/favicon.ico"):
            icon = os.path.join(HERE, "assets", "favicon.ico" if path.endswith(".ico") else "icon.png")
            if os.path.exists(icon):
                ctype = "image/x-icon" if icon.endswith(".ico") else "image/png"
                return self._send(200, open(icon, "rb").read(), ctype)
            return self._send(404, b"")
        if path in ("/", "/index.html"):
            return self._send(200, HTML, "text/html; charset=utf-8")
        if path == "/api/guard":
            cfg = settings.get("guard") or {}
            return self._json({"ok": True, "config": cfg, "state": guard_check()})
        if path == "/api/guard/test":
            threading.Thread(target=_alert_and_quit,
                             args=(95.0, float((settings.get("guard") or {}).get("ram_percent", 90))),
                             daemon=True).start()
            return self._json({"ok": True, "preview": True})
        if path == "/api/status":
            with _lock:
                gw = port_open(GW_PORT)
                rm = remote_status()
                body = {"gateway": gw, "remote": rm, "job": job_state(),
                        "hw": hw_stats(), "profiles": profiles(), "log": tail_log(60),
                        "song_history": [{"song": h["song"], "time": h["time"], "i": i}
                                         for i, h in enumerate(_history)],
                        "language": settings.get("language", "zh"),
                        "theme": settings.get("theme", "dark"),
                        "settings": {k: settings.get(k) for k in (
                            "auto_shift", "fp16", "seed", "random_seed", "full_song",
                            "lift_ratio", "repair_jumps", "jump_semitones",
                            "gate_enabled", "gate_p95", "gate_max", "comp_thr", "comp_ratio",
                            "pitch_shift", "cfg", "n_steps", "acappella", "denoise", "peak",
                            "deess_db", "consonant_mix", "clip_min", "clip_max", "lift_max")},
                        "voice_id": settings.get("voice", ""), "voice": ""}
                for p in body["profiles"]:
                    if p["id"] == body["voice_id"]:
                        body["voice"] = p["name"]
            return self._json(body)
        if path == "/api/test":
            with _lock:
                t = dict(_test)
            url = "/api/test/audio" if t.get("state") == "done" else None
            jb = job_state()
            return self._json({"state": t["state"], "msg": t.get("msg", ""), "url": url,
                               "stage": jb.get("stage", ""), "busy": jb.get("busy", False),
                               "elapsed": jb.get("elapsed", 0), "eta": jb.get("eta", 0)})
        if path == "/api/settings_full":
            return self._json({"ok": True, "settings": settings.load()})
        if path == "/api/song":
            with _lock:
                s = dict(_song)
                jb = job_state()
                hist = [{"song": h["song"], "time": h["time"], "i": i}
                        for i, h in enumerate(_history)]
            job = jobs.latest() or {}
            state = job.get("state", "idle")
            url = "/api/song/audio" if state == "done" else None
            return self._json({"state": state, "msg": job.get("error", ""),
                               "url": url, "song": job.get("song", ""),
                               "stage": jb.get("stage", ""), "busy": jb.get("busy", False),
                               "elapsed": jb.get("elapsed", 0), "eta": jb.get("eta", 0),
                               "history": jobs.history(12)})
        if path == "/api/song/audio":
            idx = 0
            dl = False
            try:
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                idx = int((q.get("i") or ["0"])[0])
                dl = (q.get("dl") or ["0"])[0] in ("1", "true", "yes")
            except Exception:
                idx = 0
            jid = str((payload if 'payload' in dir() else {}).get("id") or "")
            job = jobs.latest() or {}
            f = job.get("file") if idx == 0 else None
            if not f:
                f = jobs.history_file(idx)
            song_name = job.get("song", "cover")
            if f and os.path.exists(f):
                data = open(f, "rb").read()
                if not dl:
                    return self._send(200, data, "audio/mpeg")
                import re as _re
                from urllib.parse import quote
                safe = _re.sub(r'[\\/:*?"<>|]+', "_", str(song_name or "cover"))[:60] or "cover"
                fname = f"{safe}_{time.strftime('%H%M%S')}.mp3"
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Disposition",
                                 f"attachment; filename=\"cover.mp3\"; filename*=UTF-8''{quote(fname)}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send(404, b"")
        if path == "/api/test/audio":
            f = _test.get("file")
            if f and os.path.exists(f):
                data = open(f, "rb").read()
                return self._send(200, data, "audio/mpeg")
            return self._send(404, b"")
        if path == "/api/voice_build":
            with _lock:
                return self._json(dict(_build))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/") and not self._origin_ok():
            return self._json({"ok": False, "error": "cross-origin blocked"}, 403)
        ctype = self.headers.get("Content-Type", "")
        payload = {}
        raw = b""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            raw = self.rfile.read(length)
        if "application/json" in ctype and raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = {}

        if path == "/api/open_folder":
            try:
                os.startfile(WORK)
            except Exception:
                pass
            return self._json({"ok": True})
        if path.startswith("/api/v1/") or path.startswith("/api/access"):
            if self._api_route("POST", path, payload):
                return
        if path == "/api/settings":
            err = _validate_settings(payload or {})
            if err:
                return self._json({"ok": False, "error": err}, 400)
            cur = dict(settings.load())
            cur.update(payload or {})
            settings.save(cur)
            return self._json({"ok": True})
        if path == "/api/pref":
            cur = dict(settings.load())
            cur.update(payload or {})
            settings.save(cur)
            return self._json({"ok": True})
        if path == "/api/voice":
            vid = str(payload.get("id") or "")
            if not vid:
                return self._json({"ok": False})
            cur = dict(settings.load())
            cur["voice"] = vid
            settings.save(cur)
            name = vid
            for pr in profiles():
                if pr["id"] == vid:
                    name = pr["name"]
                    break
            return self._json({"ok": True, "id": vid, "name": name})
        if path == "/api/voice_build":
            try:
                ctype_b, _, rest = ctype.partition("boundary=")
                boundary = rest.strip().encode()
                parts = raw.split(b"--" + boundary)
                name, filename, data = "", "reference.wav", b""
                for part in parts:
                    if b"Content-Disposition" not in part:
                        continue
                    head, _, body = part.partition(b"\r\n\r\n")
                    text = head.decode("utf-8", "ignore")
                    body = body.rstrip(b"\r\n-")
                    if 'name="name"' in text:
                        name = body.decode("utf-8", "ignore")
                    elif 'name="file"' in text:
                        for seg in text.split(";"):
                            if "filename=" in seg:
                                filename = seg.split("filename=")[-1].strip().strip('"')
                        data = body
                if not data:
                    return self._json({"ok": False, "error": "no file"})
                threading.Thread(target=build_voice_profile,
                                 args=(name, data, filename), daemon=True).start()
                return self._json({"ok": True})
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc)[:120]})
        if path == "/api/test":
            with _lock:
                if _test.get("state") == "running":
                    return self._json({"ok": False, "msg": "already running"})
            vid = payload.get("voice") or settings.get("voice")
            threading.Thread(target=run_test, args=(vid,), daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/song":
            song = str(payload.get("song") or "").strip()
            if not song:
                return self._json({"ok": False, "msg": "empty song"})
            vid = payload.get("voice") or settings.get("voice")
            job = jobs.enqueue(song, vid, source="panel")
            return self._json({"ok": True, "job_id": job["id"]})
        with _lock:
            if path == "/api/start":
                start_gateway()
                start_tunnel()
                res = {"ok": True}
            elif path == "/api/stop":
                res = stop_all()
            elif path == "/api/restart":
                stop_all()
                time.sleep(1)
                start_gateway()
                start_tunnel()
                res = {"ok": True}
            elif path == "/api/tunnel/reset":
                res = reset_remote_ports()
                if res.get("ok"):
                    kill_pid(_state.get("tunnel_pid"))
                    _state["tunnel_pid"] = None
                    save_state()
                    time.sleep(1)
                    start_tunnel()
                    _cache["remote"] = (0, None)
                    time.sleep(4)
                    res["remote"] = remote_status()
            elif path == "/api/tunnel":
                kill_pid(_state.get("tunnel_pid"))
                _state["tunnel_pid"] = None
                save_state()
                start_tunnel()
                _cache["remote"] = (0, None)
                time.sleep(3)
                code = remote_status()
                res = {"ok": code in ("200", "off"), "remote": code}
            else:
                return self._json({"error": "not found"}, 404)
        return self._json(res)


def main():
    jobs.init(WORK, GW_PORT, PANEL_PORT)
    if port_open(PANEL_PORT):
        print("panel already running")
        return
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=lambda: (start_gateway(), start_tunnel()), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PANEL_PORT), Handler)
    print(f"QDLS panel: http://127.0.0.1:{PANEL_PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()

