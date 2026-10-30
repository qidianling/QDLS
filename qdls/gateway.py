"""Local GPU gateway for QDLS (Gradio API, no human-facing UI).

Endpoints the AstrBot plugin talks to:
  /convert             song url or keyword + voice id -> mp3 path
  /upload_audio        file -> staged local path (for local-audio covers)
  /show_model          voice list as text
  /list_voice_profiles voice list as json
  /build_voice         uploaded reference audio -> new voice profile
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_ROOT = os.path.dirname(HERE)
sys.path.insert(0, KIT_ROOT)
sys.path.insert(0, HERE)

os.environ.setdefault("TORCH_HOME", os.path.join(KIT_ROOT, "cache", "torch"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(KIT_ROOT, "cache"))
os.environ.setdefault("HF_HOME", os.path.join(KIT_ROOT, "hf"))
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("OMP_NUM_THREADS", "4")

# demucs 4.1 resolves its model through the HF hub first; on a machine without
# access to huggingface.co that stalls for minutes.  If the torch-hub checkpoint
# is already cached, stay offline and use it.
_demucs_ckpt = os.path.join(os.environ["TORCH_HOME"], "hub", "checkpoints",
                            "955717e8-8726e21a.th")
if not os.path.exists(_demucs_ckpt):
    # first run needs the weights; the mirror avoids huggingface timeouts in CN
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
if os.path.exists(_demucs_ckpt) and os.path.getsize(_demucs_ckpt) > 10_000_000:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import settings  # noqa: E402

# SoulX-Singer resolves its frozen Whisper encoder at import time: it uses
# WHISPER_MODEL_DIR when that directory exists, otherwise it falls back to the
# HF repo id and tries to download. Point it at the bundled copy first.
_pretrained = settings.resolve(settings.get("paths.pretrained", "./pretrained"))
_whisper_dir = os.path.join(_pretrained, "whisper-base")
if os.path.isdir(_whisper_dir):
    os.environ["WHISPER_MODEL_DIR"] = _whisper_dir
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

import engine  # noqa: E402
import sources  # noqa: E402

DEVICE = os.environ.get("COVER_DEVICE", "cuda")
FP16 = os.environ.get("COVER_FP16", "0") == "1"

_engine = None


def get_engine() -> engine.CoverEngine:
    global _engine
    want_fp16 = bool(settings.get("fp16", False))
    if _engine is not None and bool(getattr(_engine, "fp16", False)) != want_fp16:
        engine.log(f"rebuilding engine (fp16 -> {want_fp16})")
        try:
            del _engine
        except Exception:
            pass
        _engine = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
    if _engine is None:
        _engine = engine.CoverEngine(device=DEVICE, fp16=want_fp16)
    return _engine


def _profile_ids() -> list:
    return [p["profile_id"] for p in engine.list_profiles()]


def do_convert(song_name_src="", model_dropdown="", pitch_shift=None, auto_shift=False,
               n_step=None, cfg=None, auto_mix_acc=None, seed=42, random_seed=False,
               prompt_vocal_sep=True, target_vocal_sep=True, target_separation="none",
               msst_batch_size=1, msst_num_overlap=4, msst_normalize=False,
               msst_use_tta=False, msst_model="", full_song=False, progress=None):
    """Main inference entry point (signature mirrors the AstrBot plugin)."""
    engine.apply_settings()
    if isinstance(song_name_src, dict):
        song_name_src = song_name_src.get("path") or song_name_src.get("url") or ""
    is_local = isinstance(song_name_src, str) and os.path.exists(song_name_src)
    src = sources.resolve(song_name_src, log=engine.log)

    profile = str(model_dropdown or "").strip()
    ids = _profile_ids()
    if not profile or profile not in ids:
        profile = settings.get("voice") if settings.get("voice") in ids else (ids[0] if ids else "")
    if not profile:
        raise RuntimeError("no voice profile installed")

    def cb(pct, desc=""):
        if progress is not None:
            try:
                progress(pct, desc=desc)
            except Exception:
                pass

    try:
        out = get_engine().run(
            src, profile,
            pitch_shift=None if pitch_shift in (None, "") else int(pitch_shift),
            auto_shift=bool(auto_shift),
            n_steps=None if n_step in (None, "") else int(n_step),
            cfg=None if cfg in (None, "") else float(cfg),
            mix_accompaniment=None if auto_mix_acc is None else bool(auto_mix_acc),
            full_song=bool(full_song),
            lift_ratio=0.95 if is_local else 0.78,
            progress=cb,
        )
    except engine.TooHard:
        raise RuntimeError("TOO_HARD")
    except Exception as exc:
        try:
            engine.set_job_state("error: " + str(exc)[:120])
        except Exception:
            pass
        raise
    return out, "false"


def upload_audio(file):
    """Stage an uploaded file inside the kit work dir and return its path."""
    if file is None:
        return ""
    path = file if isinstance(file, str) else getattr(file, "name", "")
    if not path or not os.path.exists(path):
        return ""
    os.makedirs(engine.WORK_DIR, exist_ok=True)
    dst = os.path.join(engine.WORK_DIR, f"up_{int(time.time())}_{os.path.basename(path)}")
    shutil.copyfile(path, dst)
    return dst


def show_model():
    return "\n".join(_profile_ids())


def list_voice_profiles():
    try:
        return json.dumps(engine.list_profiles(), ensure_ascii=False)
    except Exception:
        return "[]"


def _separate_vocals(wav_path: str):
    """Prefer the karaoke model when present, else fall back to demucs."""
    kara_ckpt = os.path.join(engine.PRETRAINED, "karaoke.ckpt")
    kara_cfg = os.path.join(engine.PRETRAINED, "karaoke.yaml")
    if os.path.exists(kara_ckpt) and os.path.exists(kara_cfg):
        tools = os.path.join(engine.REPO, "preprocess", "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        try:
            from vocal_separation.model import VocalSeparator
            sep = VocalSeparator(sep_model_path=kara_ckpt, sep_config_path=kara_cfg,
                                 der_model_path="", der_config_path="",
                                 device=DEVICE, verbose=False)
            out = sep.process(wav_path, verbose=False)
            voc = out.vocals
            return (voc.mean(0) if getattr(voc, "ndim", 1) == 2 else voc), out.sample_rate
        except Exception as exc:
            engine.log(f"karaoke separation failed, falling back to demucs: {exc}")
    sep = engine.Separator(DEVICE)
    voc, _acc, sr = sep.separate(wav_path)
    return (voc.mean(0) if voc.ndim == 2 else voc), sr


def build_voice(file, name="", seconds=20.0):
    """Create a voice profile from an uploaded reference clip."""
    import numpy as np
    import soundfile as sf
    import librosa

    if file is None:
        return json.dumps({"ok": False, "error": "no file"}, ensure_ascii=False)
    path = file if isinstance(file, str) else getattr(file, "name", "")
    if not path or not os.path.exists(path):
        return json.dumps({"ok": False, "error": "file missing"}, ensure_ascii=False)

    engine.apply_settings()
    try:
        wav = engine.to_wav24k(path, os.path.join(engine.WORK_DIR, f"ref_{int(time.time())}.wav"))
        voc, sr = _separate_vocals(wav)
        voc = np.asarray(voc, dtype=np.float32)
        win = int(float(seconds) * sr)
        hop = max(1, int(0.5 * sr))
        best, best_score = 0, -1e9
        for s in range(0, max(1, len(voc) - win), hop):
            seg = voc[s:s + win]
            rms = float(np.sqrt(np.mean(seg ** 2)) + 1e-9)
            quiet = float(np.mean(np.abs(seg) < 0.01))
            score = rms * (1.0 - quiet)
            if score > best_score:
                best_score, best = score, s
        seg = voc[best:best + win]
        peak = float(np.max(np.abs(seg)) or 1.0)
        if peak > 0.95:
            seg = seg / peak * 0.95
        seg24 = librosa.resample(seg, orig_sr=sr, target_sr=engine.SR)

        pid = "voice_" + time.strftime("%m%d%H%M%S")
        outdir = os.path.join(engine.VOICES_DIR, pid)
        os.makedirs(outdir, exist_ok=True)
        sf.write(os.path.join(outdir, "ref.wav"), seg24, engine.SR)
        display = (name or "").strip() or pid
        json.dump({"display_name": display, "source": os.path.basename(path),
                   "start": round(best / sr, 1), "end": round((best + win) / sr, 1)},
                  open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        return json.dumps({"ok": True, "id": pid, "name": display,
                           "seconds": round(len(seg24) / engine.SR, 1)}, ensure_ascii=False)
    except Exception as exc:
        engine.log(f"build_voice failed: {exc}")
        return json.dumps({"ok": False, "error": str(exc)[:200]}, ensure_ascii=False)


def build_ui():
    import gradio as gr

    ids = _profile_ids() or ["taffy"]
    with gr.Blocks(title="QDLS") as demo:
        src = gr.Textbox(label="song_name_src", value="")
        model = gr.Textbox(label="model_dropdown", value=ids[0])
        pitch = gr.Textbox(label="pitch_shift", value="")
        cfg = gr.Textbox(label="cfg", value="")
        nstep = gr.Textbox(label="n_step", value="")
        autoshift = gr.Checkbox(label="auto_shift", value=False)
        mixacc = gr.Checkbox(label="auto_mix_acc", value=False)
        seed = gr.Number(label="seed", value=42)
        rnd = gr.Checkbox(label="random_seed", value=False)
        psep = gr.Checkbox(label="prompt_vocal_sep", value=True)
        tsep = gr.Checkbox(label="target_vocal_sep", value=True)
        tsepmode = gr.Textbox(label="target_separation", value="none")
        msstb = gr.Number(label="msst_batch_size", value=1)
        mssto = gr.Number(label="msst_num_overlap", value=4)
        msstn = gr.Checkbox(label="msst_normalize", value=False)
        msstt = gr.Checkbox(label="msst_use_tta", value=False)
        msstm = gr.Textbox(label="msst_model", value="")
        full = gr.Checkbox(label="full_song", value=False)
        out_file = gr.File(label="audio")
        out_flag = gr.Textbox(label="cache", value="false")
        btn = gr.Button("convert", visible=False)
        btn.click(do_convert,
                  [src, model, pitch, autoshift, nstep, cfg, mixacc, seed, rnd, psep, tsep,
                   tsepmode, msstb, mssto, msstn, msstt, msstm, full],
                  [out_file, out_flag], api_name="convert")

        up_in = gr.File(label="audio", type="filepath", visible=False)
        up_out = gr.Textbox(visible=False)
        up_btn = gr.Button("upload", visible=False)
        up_btn.click(upload_audio, [up_in], [up_out], api_name="upload_audio")

        sm_btn = gr.Button("models", visible=False)
        sm_out = gr.Textbox(visible=False)
        sm_btn.click(show_model, None, [sm_out], api_name="show_model")

        lv_btn = gr.Button("voices", visible=False)
        lv_out = gr.Textbox(visible=False)
        lv_btn.click(list_voice_profiles, None, [lv_out], api_name="list_voice_profiles")

        bv_in = gr.File(label="reference", type="filepath", visible=False)
        bv_name = gr.Textbox(label="name", value="")
        bv_out = gr.Textbox(visible=False)
        bv_btn = gr.Button("build", visible=False)
        bv_btn.click(build_voice, [bv_in, bv_name], [bv_out], api_name="build_voice")
    return demo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=settings.get("gateway.host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(settings.get("gateway.port", 6767)))
    args = ap.parse_args()
    demo = build_ui()
    demo.queue(max_size=16, default_concurrency_limit=1)
    engine.log(f"QDLS gateway on http://{args.host}:{args.port}  voices={_profile_ids()}")
    demo.launch(server_name=args.host, server_port=args.port, inbrowser=False, show_error=True)


if __name__ == "__main__":
    main()
