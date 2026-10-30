"""Source resolution for the cover gateway: URL, local path, or NetEase keyword search."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
import urllib.request

import engine

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url: str, out: str, timeout: int = 90) -> str:
    headers = {"User-Agent": UA, "Referer": "https://music.163.com/"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(out, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    return out


def probe_ok(path: str, min_seconds: float = 20.0) -> bool:
    """Reject HTML error pages / truncated clips before spending GPU time."""
    try:
        if os.path.getsize(path) < 20000:
            return False
        ff = engine.ffmpeg_bin()
        out = subprocess.run([ff, "-hide_banner", "-i", path],
                             capture_output=True, text=True, timeout=60)
        text = (out.stderr or "") + (out.stdout or "")
        for line in text.splitlines():
            if "Duration:" in line:
                stamp = line.split("Duration:")[1].split(",")[0].strip()
                if stamp.startswith("N/A"):
                    return False
                h, m, s = stamp.split(":")
                seconds = int(h) * 3600 + int(m) * 60 + float(s)
                return seconds >= min_seconds
        return False
    except Exception:
        return False


def netease_search(keyword: str, limit: int = 5) -> list:
    data = urllib.parse.urlencode({"s": keyword, "limit": limit, "type": 1, "offset": 0}).encode()
    req = urllib.request.Request(
        "http://music.163.com/api/search/get/web",
        data=data,
        headers={"User-Agent": UA, "Referer": "http://music.163.com"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.loads(r.read().decode("utf-8", "ignore"))
    return ((payload or {}).get("result") or {}).get("songs") or []


VIRTUAL_KEYS = ("\u521d\u97f3", "\u30df\u30af", "miku", "\u6d1b\u5929\u4f9d", "\u955c\u97f3", "\u5de1\u97f3",
                "gumi", "\u8a00\u548c", "\u4e50\u6b63", "vocaloid", "\u5f26\u5377", "\u6b4c\u7231", "\u7ed3\u6708")


def is_virtual(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in VIRTUAL_KEYS)


def _core_title(name: str) -> str:
    import re

    base = re.sub(r"[\uff08(].*?[\uff09)]", "", name or "")
    base = re.sub(r"\s+", "", base)
    return base[:6]


def _try_song(song: dict, prefix: str = "ne") -> str:
    sid = song.get("id")
    if not sid:
        raise RuntimeError("no id")
    out = os.path.join(engine.WORK_DIR, f"{prefix}_{sid}.mp3")
    _get(f"https://music.163.com/song/media/outer/url?id={sid}.mp3", out)
    if not probe_ok(out):
        raise RuntimeError("probe failed")
    return out


def from_cover(keyword: str, log=print) -> str:
    """Virtual-singer material: prefer a human cover, which the SVC handles far better."""
    core = _core_title(keyword.split("|")[0])
    for q in (f"{keyword.split('|')[0]} \u7ffb\u5531", f"{keyword.split('|')[0]} cover"):
        try:
            cands = netease_search(q, 10)
        except Exception:
            continue
        tries = 0
        for s in cands:
            artists = "/".join(a.get("name", "") for a in s.get("artists", []))
            if is_virtual(artists):
                continue
            if core and core not in (s.get("name") or ""):
                continue
            tries += 1
            if tries > 3:
                break
            try:
                path = _try_song(s, prefix="cover")
                log(f"cover source: {s.get('name')} - {artists} (id={s.get('id')})")
                return path
            except Exception:
                continue
    raise RuntimeError(f"no human cover found for {keyword!r}")


def from_netease(keyword: str) -> str:
    for song in netease_search(keyword, 6):
        try:
            return _try_song(song)
        except Exception:
            continue
    raise RuntimeError(f"no playable audio for {keyword!r}")


def from_txqq(keyword: str) -> str:
    data = urllib.parse.urlencode({"input": keyword, "filter": "name", "type": "qq", "page": 1}).encode()
    req = urllib.request.Request(
        "https://music.txqq.pro/", data=data,
        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.loads(r.read().decode("utf-8", "ignore"))
    songs = payload.get("songs") or []
    if not songs:
        raise RuntimeError(f"no song found for {keyword!r}")
    url = songs[0].get("url")
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".mp3"
    out = _get(url, os.path.join(engine.WORK_DIR, f"tx_{abs(hash(url)) % 10**9}{ext}"))
    if not probe_ok(out):
        raise RuntimeError("aggregator returned invalid audio")
    return out


def resolve(src: str, log=print) -> str:
    src = str(src).strip()
    if "|" in src:
        # "song name|url" hint from the plugin
        name, _, url = src.partition("|")
        name, url = name.strip(), url.strip()
    else:
        name, url = "", src

    if os.path.exists(src):
        return src

    # Virtual-singer songs: a human cover is a much better donor, try it first.
    if name and is_virtual(name):
        try:
            return from_cover(f"{name}|{url}", log=log)
        except Exception as e:
            log(f"cover search failed ({e}), using the original")

    if url.startswith("http://") or url.startswith("https://"):
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".mp3"
        out = os.path.join(engine.WORK_DIR, f"url_{abs(hash(url)) % 10**10}{ext}")
        try:
            _get(url, out)
            if probe_ok(out):
                return out
            log(f"url invalid, falling back to search: {name or url}")
        except Exception as e:
            log(f"url download failed ({e}), falling back to search")

    keyword = name or src
    try:
        return from_netease(keyword)
    except Exception as e:
        log(f"netease search failed ({e}), trying aggregator")
    return from_txqq(keyword)
