#!/usr/bin/env python
"""QDLS MCP server (stdio).

Exposes the QDLS singing engine as MCP tools so any MCP client - DeepSeek
Harness, Claude Desktop, Cursor, ... - can request a cover.

Config (environment):

    QDLS_URL    API base (panel), default http://127.0.0.1:6768
    QDLS_TOKEN  API token from the QDLS console "接入" page
    QDLS_OUT    where finished mp3 files are written, default %TEMP%\\qdls

Protocol: newline-delimited JSON-RPC 2.0 over stdio (MCP 2024-11-05).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VERSION = "1.0.0"
URL = os.environ.get("QDLS_URL", "http://127.0.0.1:6768").rstrip("/")
def _read_token() -> str:
    """Token from env, else from the file the QDLS console writes for us."""
    env = os.environ.get("QDLS_TOKEN", "").strip()
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "QDLS", "dsh.token"),
        os.path.join(here, "qdls.token"),
    ]
    for p in cands:
        try:
            if os.path.exists(p):
                t = open(p, encoding="utf-8").read().strip()
                if t:
                    return t
        except Exception:
            pass
    return ""


TOKEN = _read_token()
OUT = os.environ.get("QDLS_OUT") or os.path.join(tempfile.gettempdir(), "qdls")


def log(msg: str) -> None:
    print(f"[qdls-mcp] {msg}", file=sys.stderr, flush=True)


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    tok = TOKEN or _read_token()
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def call(method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": True, "raw": raw.decode("utf-8", "ignore")[:500]}


TOOLS = [
    {
        "name": "qdls_sing",
        "description": "用 QDLS 本地引擎把一首歌换成本地音色唱出来（只输出人声，去伴奏）。"
                       "返回生成的 mp3 路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "song": {"type": "string", "description": "歌名或音频直链，例如 雨爱 杨丞琳"},
                "voice": {"type": "string", "description": "音色 id，可留空用默认音色"},
            },
            "required": ["song"],
        },
    },
    {
        "name": "qdls_voices",
        "description": "列出 QDLS 上可用的音色 id。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "qdls_status",
        "description": "查看 QDLS 引擎状态：是否忙碌、队列长度。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def tool_sing(args: dict) -> str:
    song = str(args.get("song") or "").strip()
    if not song:
        return "错误：song 不能为空"
    voice = str(args.get("voice") or "").strip()
    job = call("POST", "/api/v1/sing", {"song": song, "voice": voice, "requester": "mcp"})
    jid = job.get("job_id")
    if not jid:
        return f"提交失败：{job.get('error') or job}"
    log(f"job {jid} for {song}")
    deadline = time.time() + 600
    state = None
    while time.time() < deadline:
        time.sleep(3)
        info = call("GET", f"/api/v1/jobs/{jid}").get("job", {})
        state = info.get("state")
        if state == "done":
            break
        if state in ("error", "cancelled"):
            return f"翻唱失败：{info.get('error') or state}"
    if state != "done":
        return "翻唱超时（10 分钟）"
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, f"qdls_{jid}.mp3")
    req = urllib.request.Request(f"{URL}/api/v1/jobs/{jid}/audio", headers=_headers())
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        f.write(r.read())
    return f"已生成：{dst}（{os.path.getsize(dst)//1024} KB）"


def tool_voices(_args: dict) -> str:
    data = call("GET", "/api/v1/voices")
    items = data.get("voices") or []
    return "可用音色：" + "、".join(f"{v['id']}({v['name']})" for v in items)


def tool_status(_args: dict) -> str:
    h = call("GET", "/api/v1/health")
    return f"QDLS：{'忙碌' if h.get('busy') else '空闲'}，队列 {h.get('queue', 0)} 首，音色 {len(h.get('voices') or [])} 个"


HANDLERS = {"qdls_sing": tool_sing, "qdls_voices": tool_voices, "qdls_status": tool_status}


def handle(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "qdls", "version": VERSION},
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"unknown tool {name}"}}
        try:
            text = fn(args)
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": f"错误：{exc}"}], "isError": True}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"unknown method {method}"}}


def main() -> None:
    if not TOKEN:
        log("QDLS_TOKEN 未设置：所有调用会被网关拒绝（401）")
    log(f"gateway={URL} out={OUT}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        try:
            resp = handle(req)
        except Exception as exc:
            log(f"handler crash: {exc}")
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": str(exc)[:200]}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
