"""QDLS bridge for AstrBot.

Talks to the QDLS REST API (`/api/v1/*`), submits a song, waits for the render
and sends the mp3 back as a voice message.

Pairing: open the QDLS console -> 接入 -> 生成配对码, then paste the gateway URL
and the token into this plugin's config.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time

import httpx

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Record
from astrbot.api.star import Context, Star, register


@register("qdls", "QDLS", "QDLS 本地翻唱引擎对接", "1.0.0")
class QdlsBridge(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

    # ------------------------------------------------------------ helpers
    @property
    def base(self) -> str:
        return str(self.config.get("gateway_url") or "http://127.0.0.1:6768").rstrip("/")

    @property
    def token(self) -> str:
        return str(self.config.get("token") or "")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _get(self, path: str, timeout: float = 20.0):
        with httpx.Client(base_url=self.base, timeout=timeout, headers=self._headers()) as c:
            r = c.get(path)
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, body: dict | None = None, timeout: float = 30.0):
        with httpx.Client(base_url=self.base, timeout=timeout, headers=self._headers()) as c:
            r = c.post(path, json=body or {})
            r.raise_for_status()
            return r.json()

    async def _say(self, event: AstrMessageEvent, text: str) -> None:
        try:
            await event.send(event.plain_result(text))
        except Exception:
            pass

    # ------------------------------------------------------------ background job
    def _worker(self, loop, event, song: str, voice: str) -> None:
        """Blocking poll in a thread; delivery is scheduled back on the loop."""
        interval = max(1, int(self.config.get("poll_interval", 3) or 3))
        timeout = max(30, int(self.config.get("timeout", 300) or 300))
        deadline = time.time() + timeout
        try:
            job = self._post("/api/v1/sing", {"song": song, "voice": voice or self.config.get("default_voice") or ""})
            jid = job.get("job_id")
            if not jid:
                raise RuntimeError(job.get("error") or "submit failed")
            logger.info(f"[QDLS] job {jid} for {song!r}")
            state = None
            while time.time() < deadline:
                time.sleep(interval)
                info = self._get(f"/api/v1/jobs/{jid}").get("job", {})
                state = info.get("state")
                if state == "done":
                    break
                if state in ("error", "cancelled"):
                    raise RuntimeError(info.get("error") or state)
            if state != "done":
                raise RuntimeError("timeout")

            with httpx.Client(base_url=self.base, timeout=120, headers=self._headers()) as c:
                r = c.get(f"/api/v1/jobs/{jid}/audio")
                r.raise_for_status()
                fd, path = tempfile.mkstemp(prefix="qdls_", suffix=".mp3")
                with os.fdopen(fd, "wb") as f:
                    f.write(r.content)
            asyncio.run_coroutine_threadsafe(self._deliver(event, path, song), loop)
        except Exception as exc:
            logger.error(f"[QDLS] job failed: {exc}")
            asyncio.run_coroutine_threadsafe(self._say(event, f"❌ 翻唱失败：{exc}"), loop)

    async def _deliver(self, event: AstrMessageEvent, path: str, song: str) -> None:
        try:
            await event.send(event.chain_result([Record(file=path)]))
        except Exception as exc:
            logger.error(f"[QDLS] send audio failed: {exc}")
            await self._say(event, "❌ 音频发送失败")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------ tools
    @filter.llm_tool(name="qdls_sing")
    async def qdls_sing(self, event: AstrMessageEvent, song: str, voice: str = ""):
        """用 QDLS 本地引擎把一首歌换成本地音色唱出来，生成后会自动发语音。

        Args:
            song(string): 歌名或音频直链，例如 "雨爱 杨丞琳"
            voice(string): 音色 id（可留空，用默认音色）
        """
        if not self.token:
            return "QDLS 插件还没配置 token：请在 QDLS 控制台「接入」页生成配对码后填入插件配置。"
        song = (song or "").strip()
        if not song:
            return "请告诉我要唱哪首歌。"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return "内部错误：没有运行中的事件循环"
        threading.Thread(target=self._worker,
                         args=(loop, event, song, (voice or "").strip()),
                         name="qdls-worker", daemon=True).start()
        return "ok"

    @filter.llm_tool(name="qdls_voices")
    async def qdls_voices(self, event: AstrMessageEvent):
        """列出 QDLS 上可用的音色 id，用于点歌时指定 voice。"""
        if not self.token:
            return "QDLS 插件未配置 token。"
        try:
            data = self._get("/api/v1/voices")
            items = data.get("voices") or []
            return "可用音色：" + "、".join(f"{v['id']}({v['name']})" for v in items)
        except Exception as exc:
            return f"查询失败：{exc}"

    @filter.llm_tool(name="qdls_status")
    async def qdls_status(self, event: AstrMessageEvent):
        """查看 QDLS 引擎当前状态：是否忙、队列长度。"""
        if not self.token:
            return "QDLS 插件未配置 token。"
        try:
            h = self._get("/api/v1/health")
            return f"QDLS：{'忙碌中' if h.get('busy') else '空闲'}，队列 {h.get('queue', 0)} 首"
        except Exception as exc:
            return f"查询失败：{exc}"

    async def terminate(self):
        logger.info("[QDLS] bridge stopped")
