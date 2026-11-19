"""TTS 管理器：按需拉起 IndexTTS2 工作进程，翻唱前回收显存。

设计要点
    * TTS 跑在独立进程（路径由 settings.json 的 tts.* 给出），进程退出显存立刻归还；
    * 翻唱任务开始前调用 release()，保证 SVC 拿到整块显存；
    * 空闲超过 tts.idle_unload_seconds 自动卸载。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

import settings

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 路径全部来自 settings.json（tts.*），仓库里不留本机绝对路径
TTS_PY = ""
TTS_SERVER = ""
TTS_HOME = ""
SVC_SITE = ""


def _paths() -> None:
    global TTS_PY, TTS_SERVER, TTS_HOME, SVC_SITE
    TTS_PY = str(_cfg("python", "") or "")
    TTS_SERVER = str(_cfg("server", "") or "")
    TTS_HOME = str(_cfg("home", "") or "")
    SVC_SITE = str(_cfg("site_packages", "") or "")
LOG = os.path.join(KIT_ROOT, "tts.log")

_lock = threading.RLock()
_proc: subprocess.Popen | None = None
_last_used = 0.0


def _cfg(key: str, default=None):
    return settings.get(f"tts.{key}", default)


def port() -> int:
    return int(_cfg("port", 6769) or 6769)


def base() -> str:
    return f"http://127.0.0.1:{port()}"


def _env() -> dict:
    env = dict(os.environ)
    # 借 SVC 环境里的 torch/torchaudio，TTS 环境只装 IndexTTS 自己的依赖
    env["PYTHONPATH"] = SVC_SITE + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["MODELSCOPE_CACHE"] = r"<KIT>\tts\ms-cache"
    env["HF_HOME"] = r"<KIT>\tts\hf"
    env["MPLCONFIGDIR"] = r"<KIT>\tts\matplotlib"
    env["HF_HUB_OFFLINE"] = "0"
    env["TRANSFORMERS_OFFLINE"] = "0"
    return env


def _healthy(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(base() + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def running() -> bool:
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is not None:
            _proc = None
        if _proc is not None:
            return True
        return _healthy(0.6)


def ensure(timeout: float = 240) -> bool:
    """启动 TTS 工作进程并等它加载完模型。"""
    global _proc, _last_used
    _paths()
    if not TTS_PY or not TTS_SERVER:
        return False
    with _lock:
        if _healthy():
            _last_used = time.time()
            return True
        if not os.path.exists(TTS_PY) or not os.path.exists(TTS_SERVER):
            return False
        model_dir = _cfg("model_dir", os.path.join(TTS_HOME, "checkpoints"))
        args = [TTS_PY, "-u", TTS_SERVER, "--model-dir", model_dir, "--port", str(port())]
        if not bool(_cfg("bf16", True)):
            args.append("--no-bf16")
        with open(LOG, "ab") as logf:
            logf.write(f"\n=== start {time.strftime('%H:%M:%S')} ===\n".encode())
            _proc = subprocess.Popen(args, cwd=TTS_HOME, env=_env(),
                                     stdout=logf, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _proc.poll() is not None:
                return False
            if _healthy(1.0):
                _last_used = time.time()
                return True
            time.sleep(1.5)
        return False


def release() -> None:
    """杀掉 TTS 进程，立刻归还显存。"""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            try:
                _proc.terminate()
                _proc.wait(timeout=8)
            except Exception:
                try:
                    _proc.kill()
                except Exception:
                    pass
        _proc = None


def idle_release(max_idle: float | None = None) -> None:
    if max_idle is None:
        max_idle = float(_cfg("idle_unload_seconds", 600) or 600)
    if max_idle <= 0:
        return
    # 内存吃紧时立刻释放（TTS 常驻要占 6 GB 左右）
    try:
        import psutil
        if psutil.virtual_memory().available < 1_200_000_000:
            release()
            return
    except Exception:
        pass
    if _last_used and time.time() - _last_used > max_idle:
        release()


def say(text: str, ref: str, emo: str = "", emo_alpha: float = 0.85,
        duration_factor: float = 1.0, lang: str = "zh") -> bytes:
    global _last_used
    if not ensure():
        raise RuntimeError("TTS 模型没起来，看看 tts.log")
    payload = json.dumps({"text": text, "ref": ref, "emo": emo or ref,
                          "emo_alpha": emo_alpha, "duration_factor": duration_factor,
                          "lang": lang}, ensure_ascii=False).encode()
    req = urllib.request.Request(base() + "/tts", data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"TTS 合成失败: {detail}") from exc
    _last_used = time.time()
    return data


def status() -> dict:
    return {"running": running(), "port": port(),
            "idle_seconds": round(time.time() - _last_used) if _last_used else 0,
            "model_dir": _cfg("model_dir", os.path.join(TTS_HOME, "checkpoints"))}
