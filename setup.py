"""QDLS setup wizard.

Guided install: probe the machine, fetch a private Python runtime when the box has
none, pick the right CUDA wheel for the installed driver, then download models
with real progress.  Serves http://127.0.0.1:6790
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("QDLS_SETUP_PORT", "6790"))
CREATE_NO_WINDOW = 0x08000000

EMBED_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
MS = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={file}"
SVC_REPO = "Soul-AILab/SoulX-Singer-SVC"
PRE_REPO = "Soul-AILab/SoulX-Singer-Preprocess"
REPO_ZIP = "https://codeload.github.com/Soul-AILab/SoulX-Singer/zip/refs/heads/main"
MIRRORS = {
    "pypi": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "pypi_host": "pypi.tuna.tsinghua.edu.cn",
}

PROGRESS = {
    "running": False, "done": False, "error": "",
    "step": "", "step_index": 0, "steps_total": 6,
    "file": "", "bytes": 0, "total": 0, "percent": 0,
    "log": [], "install_dir": "",
}
LOCK = threading.Lock()
ENV = {}


def log(msg: str) -> None:
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    with LOCK:
        PROGRESS["log"].append(line)
        del PROGRESS["log"][:-500]
    print(line, flush=True)


def set_step(idx: int, name: str) -> None:
    with LOCK:
        PROGRESS["step_index"] = idx
        PROGRESS["step"] = name
        PROGRESS["bytes"] = PROGRESS["total"] = 0
        PROGRESS["percent"] = 0
    log(f"== {name}")


def add_progress(done: int, total: int, label: str = "") -> None:
    with LOCK:
        PROGRESS["bytes"] = done
        PROGRESS["total"] = total
        PROGRESS["percent"] = int(done * 100 / total) if total else 0
        if label:
            PROGRESS["file"] = label


def free_gb(path: str) -> float:
    try:
        return round(shutil.disk_usage(path).free / 1e9, 1)
    except Exception:
        return 0.0


# ------------------------------------------------------------------ probing
def run(cmd, timeout=20):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        return out.returncode, (out.stdout or "").strip(), (out.stderr or "").strip()
    except Exception as exc:
        return -1, "", str(exc)


GPU_GFX = [
    (("7900", "7800", "7700", "7650"), "gfx1100/1101/1102 (RDNA3)"),
    (("9070", "9060"), "gfx1200 (RDNA4, ROCm 6.3+)"),
    (("6950", "6900", "6800", "6750", "6700", "6650", "6600"), "gfx1030/1031/1032 (RDNA2)"),
    (("5700", "5600"), "gfx1010 (RDNA1, 不推荐)"),
]


def _video_controllers() -> list:
    ps = ("Get-CimInstance Win32_VideoController | "
          "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress")
    rc, out, err = run(["powershell", "-NoProfile", "-Command", ps], timeout=25)
    if rc != 0 or not out:
        rc, out, err = run(["wmic", "path", "win32_VideoController", "get",
                            "name,driverversion", "/format:csv"], timeout=25)
        rows = []
        for line in (out or "").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3 and parts[1]:
                rows.append({"Name": parts[1], "DriverVersion": parts[2]})
        return rows
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def probe_gpu() -> dict:
    """Return what the machine actually has: nvidia / amd / intel / none."""
    smi = r"C:\Windows\System32\nvidia-smi.exe"
    if os.path.exists(smi):
        rc, out, err = run([smi, "--query-gpu=name,driver_version,memory.total",
                            "--format=csv,noheader,nounits"])
        if rc == 0 and out:
            parts = [p.strip() for p in out.splitlines()[0].split(",")]
            if len(parts) >= 3:
                name, driver, vram_mb = parts[0], parts[1], parts[2]
                try:
                    vram = float(vram_mb) / 1024.0
                except Exception:
                    vram = 0.0
                try:
                    drv = int(float(driver.split(".")[0]))
                except Exception:
                    drv = 0
                if drv >= 570:
                    cuda = "cu128"
                elif drv >= 528:
                    cuda = "cu121"
                elif drv >= 452:
                    cuda = "cu118"
                else:
                    cuda = ""
                return {
                    "vendor": "nvidia", "found": True, "name": name,
                    "driver": driver, "vram_gb": round(vram, 1), "cuda_tag": cuda,
                    "fp16": vram < 8.0, "clip_max": 20 if vram < 6.5 else 28,
                    "ok": bool(cuda) and vram >= 5.0,
                    "reason": "" if cuda else "驱动过旧（需要 452 以上）",
                    "plan": "cuda",
                }

    cards = _video_controllers()
    names = " ".join(str(c.get("Name") or "") for c in cards)
    low = names.lower()
    if "amd" in low or "radeon" in low:
        model = next((str(c.get("Name")) for c in cards
                      if "radeon" in str(c.get("Name", "")).lower()), names.strip())
        gfx = "未识别（需 AMD HIP SDK 查询 gfx 型号）"
        for keys, tag in GPU_GFX:
            if any(k in model for k in keys):
                gfx = tag
                break
        return {
            "vendor": "amd", "found": True, "name": model, "driver": "-",
            "vram_gb": 0.0, "cuda_tag": "", "ok": False, "fp16": True,
            "clip_max": 20, "gfx": gfx, "plan": "wsl-rocm",
            "reason": "AMD 显卡不能直接跑 CUDA；请走 WSL2 + ROCm 方案",
        }
    if "intel" in low and "arc" in low:
        return {"vendor": "intel", "found": True, "name": names.strip(), "ok": False,
                "plan": "directml", "reason": "Intel Arc 仅能尝试 DirectML，暂不支持"}
    return {"vendor": "none", "found": False, "plan": "none",
            "reason": "未找到 NVIDIA / AMD 独立显卡"}


def _python_ok(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    rc, out, err = run([path, "-c",
                        "import sys,struct;print(sys.version_info[0],sys.version_info[1],"
                        "struct.calcsize('P')*8)"], timeout=15)
    if rc != 0:
        return None
    try:
        major, minor, bits = [int(x) for x in out.split()[:3]]
    except Exception:
        return None
    if major != 3 or minor not in (10, 11) or bits != 64:
        return None
    return {"path": path, "version": f"{major}.{minor}", "bits": bits}


def find_python() -> dict | None:
    cands = []
    runtime = os.path.join(HERE, "runtime", "python.exe")
    cands.append(runtime)
    cfg = os.path.join(HERE, "settings.json")
    if os.path.exists(cfg):
        try:
            py = json.load(open(cfg, encoding="utf-8-sig")).get("paths", {}).get("python")
            if py:
                cands.append(py.replace("/", "\\"))
        except Exception:
            pass
    cands.append(sys.executable)
    w = shutil.which("python")
    if w:
        cands.append(w)
    for p in glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python3*", "python.exe")):
        cands.append(p)
    for p in glob.glob(r"C:\Python3*\python.exe") + glob.glob(r"C:\Program Files\Python3*\python.exe"):
        cands.append(p)
    for c in cands:
        info = _python_ok(c)
        if info:
            info["bundled"] = c == runtime
            return info
    # py launcher
    rc, out, _ = run(["py", "-3.11", "-c", "import sys;print(sys.executable)"])
    if rc == 0 and out:
        info = _python_ok(out.strip())
        if info:
            return info
    return None


def probe_env() -> dict:
    global ENV
    py = find_python()
    gpu = probe_gpu()
    ENV = {
        "python": py, "gpu": gpu,
        "free_gb": free_gb(HERE),
        "default_dir": os.path.join(os.path.expanduser("~"), "QDLS"),
        "here": HERE,
        "need_python": py is None,
    }
    return ENV


# ------------------------------------------------------------------ downloads
def download(url: str, dst: str, label: str = "") -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and os.path.getsize(dst) > 1024:
        log(f"skip (exists): {os.path.basename(dst)}")
        return
    tmp = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "QDLS-setup/1.1"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            add_progress(done, total, label or os.path.basename(dst))
    os.replace(tmp, dst)
    log(f"saved {dst} ({os.path.getsize(dst)/1e6:.0f} MB)")


def install_runtime() -> str:
    """Download an embeddable Python and bootstrap pip into runtime\\."""
    runtime = os.path.join(HERE, "runtime")
    py = os.path.join(runtime, "python.exe")
    if os.path.exists(py) and _python_ok(py):
        log("bundled runtime already present")
        return py
    os.makedirs(runtime, exist_ok=True)
    zpath = os.path.join(runtime, "python-embed.zip")
    download(EMBED_URL, zpath, "python-3.11.9-embed-amd64.zip")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(runtime)
    os.remove(zpath)

    # enable site-packages so pip works
    for pth in glob.glob(os.path.join(runtime, "python*._pth")):
        lines = open(pth, encoding="utf-8").read().splitlines()
        out = []
        for ln in lines:
            if ln.strip() in ("#import site", "import site"):
                out.append("import site")
            elif ln.strip() in ("Lib\\site-packages", "Lib/site-packages"):
                out.append(ln)
            else:
                out.append(ln)
        if not any(l.strip() == "Lib\\site-packages" for l in out):
            out.insert(0, "Lib\\site-packages")
        if not any(l.strip() == "import site" for l in out):
            out.append("import site")
        open(pth, "w", encoding="utf-8").write("\n".join(out) + "\n")
        log(f"patched {os.path.basename(pth)}")

    getpip = os.path.join(runtime, "get-pip.py")
    download(GET_PIP_URL, getpip, "get-pip.py")
    log("bootstrapping pip ...")
    rc, out, err = run([py, getpip, "--no-warn-script-location"], timeout=600)
    log((out or "")[-800:])
    if rc != 0:
        log((err or "")[-500:])
        raise RuntimeError("pip bootstrap failed")
    os.remove(getpip)
    info = _python_ok(py)
    if not info:
        raise RuntimeError("bundled python failed to start")
    log(f"runtime ready: {py}")
    return py


def pip_install(python: str, target: str, cuda_tag: str) -> None:
    req = os.path.join(HERE, "requirements.txt")
    mirror = ["-i", MIRRORS["pypi"], "--trusted-host", MIRRORS["pypi_host"]]
    extra = ["--extra-index-url", f"https://download.pytorch.org/whl/{cuda_tag}"] if cuda_tag else []

    def call(args, label):
        log(label)
        proc = subprocess.Popen(args, cwd=target, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=CREATE_NO_WINDOW)
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            log(line)
            m = re.search(r"(Downloading|Using cached)\s+(\S+)", line)
            if m:
                add_progress(0, 0, m.group(2)[:60])
        proc.wait()
        return proc.returncode

    base = [python, "-m", "pip", "install", "--no-cache-dir", "--progress-bar", "off",
            "--no-warn-script-location"]
    rc = call(base + ["torch==2.8.0", "torchaudio==2.8.0"] + extra + mirror,
              f"installing torch ({cuda_tag or 'default'}) ...")
    if rc != 0:
        log("torch install failed, retrying with default index")
        rc = call(base + ["torch==2.8.0", "torchaudio==2.8.0"] + mirror, "installing torch (fallback) ...")
        if rc != 0:
            raise RuntimeError("torch install failed")

    rest = [ln.strip() for ln in open(req, encoding="utf-8").read().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
            and not ln.strip().startswith("--") and "torch" not in ln.strip()]
    rc = call(base + rest + mirror, "installing remaining packages ...")
    if rc != 0:
        raise RuntimeError("dependency install failed")


def fetch_repo(target: str) -> str:
    repo = os.path.join(target, "SoulX-Singer")
    if os.path.isdir(os.path.join(repo, "preprocess", "tools")):
        log("SoulX-Singer already present")
        return repo
    if shutil.which("git"):
        log("cloning SoulX-Singer via git")
        rc, out, err = run(["git", "clone", "--depth", "1",
                            "https://github.com/Soul-AILab/SoulX-Singer.git", repo], timeout=600)
        if rc == 0:
            return repo
        log("git clone failed, falling back to zip")
    zpath = os.path.join(target, "soulx.zip")
    download(REPO_ZIP, zpath, "SoulX-Singer.zip")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(target)
    for name in os.listdir(target):
        if name.lower().startswith("soulx-singer-") and os.path.isdir(os.path.join(target, name)):
            os.replace(os.path.join(target, name), repo)
            break
    os.remove(zpath)
    return repo


def write_settings(target: str, repo: str, sys_py: str, env: dict) -> None:
    tmpl = os.path.join(HERE, "settings.example.json")
    data = json.load(open(tmpl, encoding="utf-8-sig")) if os.path.exists(tmpl) else {}
    data.setdefault("paths", {})
    data["paths"].update({
        "repo": repo.replace("\\", "/"),
        "pretrained": os.path.join(target, "pretrained").replace("\\", "/"),
        "voices": "./voices",
        "work": "./work",
        "python": sys_py.replace("\\", "/"),
    })
    gpu = (env or {}).get("gpu") or {}
    if gpu.get("found"):
        data["fp16"] = bool(gpu.get("fp16"))
        data["clip_max"] = int(gpu.get("clip_max", 28))
    out = os.path.join(target, "settings.json")
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"wrote {out}")


def write_dsh_bundle(target: str, python: str) -> None:
    """Fill the DSH bundle with this machine's absolute paths."""
    bundle = os.path.join(target, "dsh-bundle")
    if not os.path.isdir(bundle):
        return
    py = str(python).replace("\\", "/")
    script = os.path.join(target, "qdls_mcp.py").replace("\\", "/")
    cwd = str(target).replace("\\", "/")
    text = (
        "- insert:\n"
        "    - id: qdls-mcp\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        "        serverName: qdls\n"
        "        transport: stdio\n"
        f"        command: '{py}'\n"
        "        args:\n"
        f"          - '{script}'\n"
        "        env:\n"
        "          QDLS_URL: 'http://127.0.0.1:6768'\n"
        "          PYTHONUTF8: '1'\n"
        "          PYTHONIOENCODING: 'utf-8'\n"
        f"        cwd: '{cwd}'\n"
        "        failOnStartupError: true\n"
    )
    open(os.path.join(bundle, "cordis.patch.yml"), "w", encoding="utf-8").write(text)
    log("wrote dsh-bundle\\cordis.patch.yml for this machine")


def copy_app(target: str) -> None:
    for name in ("qdls", "tools", "samples", "voices", "docs", "launch.vbs", "icon.png"):
        src = os.path.join(HERE, name)
        dst = os.path.join(target, name)
        if not os.path.exists(src):
            continue
        if os.path.isdir(src):
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "_archive", "_downloads"))
        else:
            shutil.copy2(src, dst)
    log("copied application files")


def make_shortcut(target: str) -> None:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    lnk = os.path.join(desktop, "QDLS.lnk")
    # Explorer cannot read icons inside low-integrity folders, so keep the .ico
    # in the user profile and point the shortcut there.
    icon_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "QDLS")
    icon = os.path.join(icon_dir, "icon.ico")
    src_icon = os.path.join(target, "qdls", "assets", "icon.ico")
    try:
        os.makedirs(icon_dir, exist_ok=True)
        if os.path.exists(src_icon):
            shutil.copyfile(src_icon, icon)
    except Exception:
        icon = src_icon
    ps = (
        f"$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{lnk}');"
        f"$s.TargetPath='wscript.exe';"
        f"$s.Arguments='\"{os.path.join(target, 'launch.vbs')}\"';"
        f"$s.WorkingDirectory='{target}';"
        + (f"$s.IconLocation='{icon},0';" if os.path.exists(icon) else "")
        + f"$s.Description='QDLS - local singing voice conversion';"
        f"$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, creationflags=CREATE_NO_WINDOW)
    log(f"shortcut: {lnk} (icon {icon})")


def install(opts: dict) -> None:
    target = opts.get("dir") or os.path.join(os.path.expanduser("~"), "QDLS")
    comp = opts.get("components") or {}
    env = probe_env()
    with LOCK:
        PROGRESS.update({"running": True, "done": False, "error": "",
                         "install_dir": target, "log": []})
    try:
        os.makedirs(target, exist_ok=True)

        set_step(1, "准备安装目录")
        copy_app(target)

        set_step(2, "准备 Python 运行时")
        py = opts.get("python")
        if not (py and _python_ok(py)):
            py = env.get("python", {}).get("path") if env.get("python") else None
        if not (py and _python_ok(py)):
            log("no usable Python found -> downloading an embedded runtime")
            py = install_runtime()
        else:
            log(f"using python: {py}")

        gpu = (env.get("gpu") or {})
        if gpu.get("plan") == "wsl-rocm":
            raise RuntimeError("检测到 AMD 显卡：QDLS 需要 CUDA。请按 docs/amd-wsl-rocm.md "
                               "在 WSL2 + ROCm 里运行网关，Windows 侧只用控制台。")
        if gpu.get("plan") == "none":
            raise RuntimeError("未检测到可用的 NVIDIA 显卡；CPU 推理不现实，已停止安装。")
        cuda_tag = gpu.get("cuda_tag") or ""
        if gpu.get("found"):
            log(f"GPU: {gpu.get('name')} · {gpu.get('vram_gb')} GB · driver {gpu.get('driver')} -> {cuda_tag or 'no cuda'}")
        else:
            log(f"GPU check: {gpu.get('reason')}")

        if comp.get("deps", True):
            set_step(3, "安装 Python 依赖")
            pip_install(py, target, cuda_tag)
        else:
            log("skip dependencies (already installed)")

        set_step(4, "获取 SoulX-Singer 引擎")
        existing = str(opts.get("repo_path") or "").strip()
        if existing and os.path.isdir(os.path.join(existing, "preprocess", "tools")):
            repo = existing
            log(f"使用已有引擎目录: {repo}")
        else:
            repo = fetch_repo(target)

        if comp.get("svc", True):
            set_step(5, "下载 SVC 模型（约 2.8 GB）")
            download(MS.format(repo=SVC_REPO, file="model-svc.pt"),
                     os.path.join(target, "pretrained", "model-svc.pt"), "model-svc.pt")
            try:
                download(MS.format(repo=PRE_REPO, file="rmvpe.pt"),
                         os.path.join(target, "pretrained", "rmvpe.pt"), "rmvpe.pt")
            except Exception as exc:
                log(f"rmvpe auto-download failed: {exc}")
        else:
            log("skip models")

        if comp.get("karaoke", False):
            set_step(6, "下载卡拉 OK 分离模型（1.7 GB）")
            for name, dst in (("mel-band-roformer-karaoke/mel_band_roformer_karaoke_becruily.ckpt", "karaoke.ckpt"),
                              ("mel-band-roformer-karaoke/config_karaoke_becruily.yaml", "karaoke.yaml")):
                download(MS.format(repo=PRE_REPO, file=name),
                         os.path.join(target, "pretrained", dst), os.path.basename(dst))
        else:
            log("skip karaoke model")

        if comp.get("demucs", True):
            set_step(6, "准备 Demucs 人声分离权重")
            envp = dict(os.environ, TORCH_HOME=os.path.join(target, "cache", "torch"),
                        HF_HOME=os.path.join(target, "hf"),
                        HF_ENDPOINT=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"))
            rc, out, err = run([py, "-c",
                                "import demucs.pretrained as d; d.get_model('htdemucs')"], timeout=900)
            log((out or "")[-400:] or "demucs ready")
            if rc != 0:
                log((err or "")[-400:] or "demucs prefetch failed")

        write_settings(target, repo, py, env)
        write_dsh_bundle(target, py)
        make_shortcut(target)
        with LOCK:
            PROGRESS.update({"running": False, "done": True, "step": "完成", "percent": 100})
        log("安装完成，请双击桌面 QDLS 启动")
    except Exception as exc:
        log(f"ERROR: {exc}")
        with LOCK:
            PROGRESS.update({"running": False, "done": False, "error": str(exc)[:300]})


HTML = open(os.path.join(HERE, "setup_ui.html"), encoding="utf-8").read()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        return self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/icon.png", "/favicon.ico"):
            icon = os.path.join(HERE, "qdls", "assets",
                                "favicon.ico" if path.endswith(".ico") else "icon.png")
            if os.path.exists(icon):
                return self._send(200, open(icon, "rb").read(),
                                  "image/x-icon" if icon.endswith(".ico") else "image/png")
            return self._send(404, b"")
        if path in ("/", "/index.html"):
            return self._send(200, HTML, "text/html; charset=utf-8")
        if path == "/api/env":
            return self._json(probe_env())
        if path == "/api/progress":
            with LOCK:
                return self._json(dict(PROGRESS))
        return self._send(404, b"")

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        payload = {}
        if length:
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                payload = {}
        if path == "/api/browse":
            return self._json({"path": pick_folder()})
        if path == "/api/check_dir":
            d = str(payload.get("dir") or "")
            return self._json({"exists": os.path.isdir(d), "free_gb": free_gb(d or "~"),
                               "writable": os.access(d or os.path.expanduser("~"), os.W_OK)})
        if path == "/api/install":
            with LOCK:
                if PROGRESS["running"]:
                    return self._json({"ok": False, "msg": "already running"})
            threading.Thread(target=install, args=(payload,), daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/open_amd":
            target = os.path.join(HERE, "docs", "amd-wsl-rocm.md")
            if os.path.exists(target):
                os.startfile(target)
            return self._json({"ok": True})
        if path == "/api/open":
            d = str(payload.get("dir") or "")
            if d and os.path.isdir(d):
                os.startfile(d)
            return self._json({"ok": True})
        return self._send(404, b"")


def pick_folder() -> str:
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description='Select the QDLS install folder';"
        "if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                             capture_output=True, text=True, timeout=180,
                             encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def main():
    print(f"QDLS setup: http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
