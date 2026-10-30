"""Download the SoulX-Singer checkpoints QDLS needs.

Usage:  python tools/fetch_models.py [--karaoke]

Everything lands in ./pretrained (configurable through settings.json).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(KIT_ROOT, "pretrained")

SVC_REPO = "Soul-AILab/SoulX-Singer-SVC"
PRE_REPO = "Soul-AILab/SoulX-Singer-Preprocess"

MS = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={file}"

FILES = {
    "svc": [
        # main zero-shot singing voice conversion checkpoint (~2.8 GB)
        ("mel-band-roformer-karaoke/../model-svc.pt", "model-svc.pt"),
    ],
}

KARAOKE = [
    ("mel-band-roformer-karaoke/mel_band_roformer_karaoke_becruily.ckpt", "karaoke.ckpt"),
    ("mel-band-roformer-karaoke/config_karaoke_becruily.yaml", "karaoke.yaml"),
]


def download(url: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and os.path.getsize(dst) > 1024:
        print(f"  exists: {dst}")
        return
    tmp = dst + ".part"
    print(f"  -> {url}")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stdout.write(f"\r     {pct:3d}%  {done/1e6:6.0f} / {total/1e6:.0f} MB")
                sys.stdout.flush()
    print()
    os.replace(tmp, dst)


def try_modelscope(repo: str, out_dir: str, patterns=None) -> bool:
    try:
        from modelscope import snapshot_download
    except Exception:
        return False
    try:
        snapshot_download(repo, local_dir=out_dir, allow_patterns=patterns)
        return True
    except Exception as exc:
        print(f"  modelscope snapshot failed: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--karaoke", action="store_true", help="also fetch the 1.7 GB karaoke model")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    args = ap.parse_args()

    out = os.path.abspath(args.dir)
    os.makedirs(out, exist_ok=True)
    print(f"models -> {out}")

    ok = try_modelscope(SVC_REPO, out, patterns=["*.pt", "*.yaml", "*.json"])
    if not ok:
        print("modelscope not available, direct download:")
        download(MS.format(repo=SVC_REPO, file="model-svc.pt"),
                 os.path.join(out, "model-svc.pt"))

    # rmvpe pitch extractor lives in the preprocessing repo
    try_modelscope(PRE_REPO, out, patterns=["rmvpe*", "*rmvpe*"])

    if args.karaoke:
        for name, dst in KARAOKE:
            download(MS.format(repo=PRE_REPO, file=name), os.path.join(out, dst))

    # Demucs weights (torch hub) — pre-fetch so the first cover does not stall
    print("demucs weights:")
    try:
        import demucs.pretrained as dp
        dp.get_model("htdemucs")
        print("  ok")
    except Exception as exc:
        print(f"  skipped: {exc}")

    wanted = ["model-svc.pt", "rmvpe.pt"]
    missing = [w for w in wanted if not os.path.exists(os.path.join(out, w))]
    if missing:
        print("missing checkpoints:", ", ".join(missing))
        print("If automatic download failed, download them manually from")
        print("  https://modelscope.cn/models/Soul-AILab/SoulX-Singer-SVC")
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
