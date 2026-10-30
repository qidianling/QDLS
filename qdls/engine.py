"""SoulX-Singer SVC cover engine: separation -> F0 -> zero-shot SVC -> mix."""
from __future__ import annotations

import gc
import json
import os
import shutil
import subprocess
import sys
import time
import random
import uuid
from typing import Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch

import polish
import settings as kit_settings

ROOT = os.path.dirname(os.path.abspath(__file__))
KIT_ROOT = os.path.dirname(ROOT)


def _locate_repo() -> str:
    """Find the SoulX-Singer checkout, tolerating a moved/renamed folder."""
    cands = []
    env = os.environ.get("SOULX_REPO")
    if env:
        cands.append(env)
    cands.append(kit_settings.resolve(kit_settings.get("paths.repo", "./SoulX-Singer")))
    cands.append(os.path.join(KIT_ROOT, "SoulX-Singer"))
    cands.append(os.path.join(ROOT, "SoulX-Singer"))
    # tolerate a checkout living next to the kit or in a sibling folder
    here = KIT_ROOT
    for _ in range(3):
        here = os.path.dirname(here)
        if not here or here == os.path.dirname(here):
            break
        cands.append(os.path.join(here, "SoulX-Singer"))
        cands.append(os.path.join(here, "cover", "SoulX-Singer"))
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "preprocess", "tools")):
            return c
    return cands[1]


REPO = _locate_repo()
PRETRAINED = os.environ.get("SOULX_MODELS") or kit_settings.resolve(kit_settings.get("paths.pretrained", "./pretrained"))
VOICES_DIR = os.environ.get("SOULX_VOICES") or kit_settings.resolve(kit_settings.get("paths.voices", "./voices"))
WORK_DIR = os.environ.get("SOULX_WORK") or kit_settings.resolve(kit_settings.get("paths.work", "./work"))
CACHE_DIR = os.path.join(WORK_DIR, "cache")

for p in (REPO, os.path.join(REPO, "preprocess", "tools")):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

if not os.path.isdir(os.path.join(REPO, "preprocess", "tools")):
    print("[engine] WARNING: SoulX-Singer repo not found; set paths.repo in settings.json "
          f"(tried {REPO})", flush=True)

os.environ.setdefault("WHISPER_MODEL_DIR", os.path.join(PRETRAINED, "whisper-base"))

os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(VOICES_DIR, exist_ok=True)

SR = 24000
HOP = 480
CLIP_SECONDS = 28.0
CLIP_MIN = 15.0
CLIP_MAX = 28.0
POLISH = True
CONSONANT_MIX = 0.25
DEESS_DB = -3.5
COMP_THR = -26.0
COMP_RATIO = 4.5
LIMIT_DB = -3.0
LIMIT_ON = True
DENOISE = 1.1
DENOISE_ON = True
N_STEPS_DEFAULT = 40
DEFAULT_CFG = 2.5
DEFAULT_PITCH = 0
ACAPPELLA = True
LIFT_MAX = 5
LIFT_RATIO = 0.78
AUTO_SHIFT = False
FULL_SONG = False
SEED = 42
RANDOM_SEED = False
FP16_WANTED = False
AUTO_TRANSPOSE = False
HIGH_F0_LIMIT = 620.0
REPAIR_JUMPS = True
JUMP_SEMITONES = 6.0
GATE_ENABLED = False
GATE_PITCH_ERR = 1.2
GATE_BAD_RATIO = 0.35
GATE_P95 = 650.0
GATE_MAX = 750.0


def apply_settings() -> None:
    """Re-read the tunable knobs from settings.json (called per job)."""
    global CLIP_MIN, CLIP_MAX, CONSONANT_MIX, DEESS_DB, LIMIT_DB, LIMIT_ON
    global DENOISE, DENOISE_ON, N_STEPS_DEFAULT, DEFAULT_CFG, DEFAULT_PITCH
    global ACAPPELLA, LIFT_MAX, LIFT_RATIO, AUTO_SHIFT, FULL_SONG, SEED, RANDOM_SEED
    global FP16_WANTED, REPAIR_JUMPS, JUMP_SEMITONES, GATE_ENABLED, GATE_P95, GATE_MAX
    global COMP_THR, COMP_RATIO

    kit_settings.reload()
    CLIP_MIN = float(kit_settings.get("clip_min", 15) or 15)
    CLIP_MAX = float(kit_settings.get("clip_max", 28) or 28)
    if CLIP_MAX < CLIP_MIN:
        CLIP_MAX = CLIP_MIN
    CONSONANT_MIX = float(kit_settings.get("consonant_mix", 0.25) or 0.0)
    DEESS_DB = float(kit_settings.get("deess_db", -3.5) or 0.0)
    DENOISE_ON = bool(kit_settings.get("denoise.enabled", True))
    DENOISE = float(kit_settings.get("denoise.strength", 1.0) or 0.0)
    LIMIT_ON = bool(kit_settings.get("peak.enabled", True))
    LIMIT_DB = float(kit_settings.get("peak.ceiling_db", -3.0) or -3.0)
    N_STEPS_DEFAULT = int(kit_settings.get("n_steps", 40) or 40)
    DEFAULT_CFG = float(kit_settings.get("cfg", 2.5) or 2.5)
    DEFAULT_PITCH = int(kit_settings.get("pitch_shift", 0) or 0)
    ACAPPELLA = bool(kit_settings.get("acappella", True))
    LIFT_MAX = int(kit_settings.get("lift_max", 5) or 0)
    LIFT_RATIO = float(kit_settings.get("lift_ratio", 0.78) or 0.78)
    AUTO_SHIFT = bool(kit_settings.get("auto_shift", False))
    FULL_SONG = bool(kit_settings.get("full_song", False))
    SEED = int(kit_settings.get("seed", 42) or 0)
    RANDOM_SEED = bool(kit_settings.get("random_seed", False))
    FP16_WANTED = bool(kit_settings.get("fp16", False))
    REPAIR_JUMPS = bool(kit_settings.get("repair_jumps", True))
    JUMP_SEMITONES = float(kit_settings.get("jump_semitones", 6) or 6)
    GATE_ENABLED = bool(kit_settings.get("gate_enabled", False))
    GATE_P95 = float(kit_settings.get("gate_p95", 650) or 650)
    GATE_MAX = float(kit_settings.get("gate_max", 750) or 750)
    COMP_THR = float(kit_settings.get("comp_thr", -26) or -26)
    COMP_RATIO = float(kit_settings.get("comp_ratio", 4.5) or 4.5)


apply_settings()


class TooHard(Exception):
    """Raised when a probe shows the model cannot hold this song's melody."""

    def __init__(self, metrics: dict):
        self.metrics = metrics or {}
        super().__init__("song outside comfort zone")


def excerpt_bounds(x: np.ndarray, sr: int, seconds: float) -> Tuple[int, int]:
    """Pick the most energetic window so the clip lands on a chorus, not a intro."""
    n = int(x.shape[-1])
    win = int(seconds * sr)
    if win <= 0 or n <= win:
        return 0, n
    hop = max(1, int(0.5 * sr))
    best, best_score = 0, -1.0
    for start in range(0, n - win + 1, hop):
        seg = x[start:start + win]
        score = float(np.sqrt(np.mean(seg.astype(np.float32) ** 2)))
        if score > best_score:
            best_score, best = score, start
    return best, best + win


def log(msg: str) -> None:
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    print(line, flush=True)
    try:
        with open(os.path.join(ROOT, "gateway.log"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- ffmpeg
def ffmpeg_bin() -> str:
    env = os.environ.get("FFMPEG_BIN")
    if env and os.path.exists(env):
        return env
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    for cand in (
        shutil.which("ffmpeg"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.join(ROOT, "bin", "ffmpeg.exe"),
    ):
        if cand and os.path.exists(cand):
            return cand
    raise RuntimeError("ffmpeg not found; set FFMPEG_BIN")


def to_wav24k(src: str, dst: str) -> str:
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", dst]
    subprocess.run(cmd, check=True)
    return dst


def to_mp3(src: str, dst: str, bitrate: str = "192k") -> str:
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", bitrate, dst]
    subprocess.run(cmd, check=True)
    return dst


def load_audio(path: str, sr: int = SR) -> torch.Tensor:
    wav, file_sr = sf.read(path, always_2d=True, dtype="float32")
    wav = wav.mean(axis=1)
    if file_sr != sr:
        import librosa

        wav = librosa.resample(wav, orig_sr=file_sr, target_sr=sr)
    return torch.from_numpy(np.ascontiguousarray(wav)).unsqueeze(0)


# ---------------------------------------------------------------- separation
class Separator:
    def __init__(self, device: str = "cuda"):
        self.device = device
        self._sep = None

    def _load(self):
        if self._sep is None:
            from demucs.api import Separator as DemucsSeparator

            log("loading demucs htdemucs ...")
            self._sep = DemucsSeparator(model="htdemucs", device=self.device, progress=False)
            log("demucs ready")
        return self._sep

    def separate(self, wav_path: str) -> Tuple[np.ndarray, np.ndarray, int]:
        sep = self._load()
        _, stems = sep.separate_audio_file(wav_path)
        vocals = stems["vocals"].detach().cpu().numpy()
        mix = sum(t.detach().cpu().numpy() for t in stems.values())
        acc = mix - vocals
        return vocals, acc, sep.samplerate


# ---------------------------------------------------------------- f0
class F0:
    def __init__(self, device: str = "cuda"):
        try:
            from f0_extraction import F0Extractor
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "SoulX-Singer not found: set paths.repo in settings.json "
                f"(tried {REPO}). Clone it with: git clone "
                "https://github.com/Soul-AILab/SoulX-Singer"
            ) from exc

        model_path = os.path.join(PRETRAINED, "rmvpe.pt")
        self.extractor = F0Extractor(
            model_path, device=device, is_half=(device == "cuda"),
            input_sr=16000, target_sr=SR, hop_size=HOP, verbose=False,
        )

    def __call__(self, wav_path: str) -> np.ndarray:
        return self.extractor.process(wav_path, verbose=False)


# ---------------------------------------------------------------- model
class SVCModel:
    def __init__(self, device: str = "cuda", fp16: bool = True):
        from soulxsinger.utils.file_utils import load_config
        from soulxsinger.models.soulxsinger_svc import SoulXSingerSVC

        self.device = device
        self.fp16 = fp16
        cfg = load_config(os.path.join(REPO, "soulxsinger", "config", "soulxsinger.yaml"))
        self.cfg = cfg
        self.sr = int(cfg.audio.sample_rate)
        log("loading SoulX-Singer SVC checkpoint ...")
        model = SoulXSingerSVC(cfg)
        path = os.path.join(PRETRAINED, "model-svc.pt")
        try:
            # mmap keeps the 2.8 GB checkpoint off the heap while it is copied to GPU
            ckpt = torch.load(path, map_location="cpu", weights_only=False, mmap=True)
        except TypeError:
            ckpt = torch.load(path, map_location="cpu", weights_only=False)
        state = ckpt["state_dict"] if "state_dict" in ckpt else ckpt
        model.load_state_dict(state, strict=True)
        del ckpt, state
        model = model.to(device).eval()
        if fp16 and device.startswith("cuda"):
            model.half()
            model.mel.float()
        self.model = model
        gc.collect()
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        log("SVC model ready")

    @torch.no_grad()
    def convert(self, prompt_wav: torch.Tensor, prompt_f0: np.ndarray,
                target_wav: torch.Tensor, target_f0: np.ndarray,
                pitch_shift: int = 0, auto_shift: bool = True,
                n_steps: int = 32, cfg: float = 3.0) -> Tuple[np.ndarray, int]:
        pt_f0 = torch.from_numpy(prompt_f0).float().unsqueeze(0).to(self.device)
        gt_f0 = torch.from_numpy(target_f0).float().unsqueeze(0).to(self.device)
        audio, shift = self.model.infer(
            pt_wav=prompt_wav.to(self.device),
            gt_wav=target_wav.to(self.device),
            pt_f0=pt_f0,
            gt_f0=gt_f0,
            auto_shift=auto_shift,
            pitch_shift=int(pitch_shift or 0),
            n_steps=int(n_steps),
            cfg=float(cfg),
            use_fp16=self.fp16,
        )
        return audio.squeeze().float().cpu().numpy(), int(shift)


# ---------------------------------------------------------------- voices
def list_profiles() -> List[Dict[str, str]]:
    out = []
    for name in sorted(os.listdir(VOICES_DIR)):
        if name.startswith("_") or name.startswith("."):
            continue  # backups / scratch dirs are not selectable voices
        d = os.path.join(VOICES_DIR, name)
        if not os.path.isdir(d):
            continue
        ref = os.path.join(d, "ref.wav")
        if not os.path.exists(ref):
            continue
        meta = {}
        mpath = os.path.join(d, "meta.json")
        if os.path.exists(mpath):
            try:
                meta = json.load(open(mpath, encoding="utf-8"))
            except Exception:
                meta = {}
        out.append({"profile_id": name, "display_name": meta.get("display_name") or name})
    return out


def _fmt_dur(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


STATE_FILE = os.path.join(WORK_DIR, "job_state.json")


def set_job_state(stage: str, job: str = "", started: float | None = None) -> None:
    """Tiny status file the control panel polls (busy / stage / elapsed)."""
    try:
        json.dump({"job": job, "stage": stage, "started": started or time.time(),
                   "updated": time.time(), "pid": os.getpid()},
                  open(STATE_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


# ---------------------------------------------------------------- pipeline
def _release(keep: str = "") -> None:
    """Give the OS/GPU its memory back after a job instead of letting it accumulate."""
    try:
        import torch as _t

        gc.collect()
        if _t.cuda.is_available():
            _t.cuda.empty_cache()
            _t.cuda.ipc_collect()
    except Exception:
        pass
    # remove old job folders (keep the newest one, which holds the mp3 we return)
    try:
        jobs = [os.path.join(WORK_DIR, d) for d in os.listdir(WORK_DIR)]
        jobs = [j for j in jobs if os.path.isdir(j) and os.path.abspath(j) != os.path.abspath(keep)]
        jobs.sort(key=os.path.getmtime)
        for d in jobs[:-3]:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


class CoverEngine:
    def __init__(self, device: str = "cuda", fp16: bool = True):
        self.device = device
        self.sep = Separator(device)
        self.f0 = F0(device)
        self.svc = SVCModel(device, fp16)
        self._profile_cache: Dict[str, tuple] = {}

    def profile(self, profile_id: str):
        if profile_id in self._profile_cache:
            return self._profile_cache[profile_id]
        d = os.path.join(VOICES_DIR, profile_id)
        ref = os.path.join(d, "ref.wav")
        if not os.path.exists(ref):
            raise FileNotFoundError(f"voice profile not found: {profile_id}")
        f0path = os.path.join(d, "ref_f0.npy")
        if os.path.exists(f0path):
            f0 = np.load(f0path)
        else:
            f0 = self.f0(ref)
            np.save(f0path, f0)
        f0 = polish.smooth_f0(f0)
        wav = load_audio(ref, self.svc.sr)
        self._profile_cache[profile_id] = (wav, f0)
        return wav, f0

    def _repair_jumps(self, audio, prompt_wav, prompt_f0, target_wav, target_f0,
                      cfg: float = 2.5, n_steps: int = 40,
                      pitch_shift: int = 0) -> Tuple[np.ndarray, int]:
        """Re-infer only the windows around impossible f0 jumps, then cross-fade them in.

        The melody itself is never modified: the window is regenerated with a more
        conservative sampler and spliced back at equal loudness.
        """
        sr = self.svc.sr
        f0 = np.asarray(target_f0, dtype=np.float32)
        if f0.size < 3:
            return audio, 0
        logf = np.log2(np.maximum(f0, 1e-6))
        step_semi = np.abs(np.diff(logf)) * 12.0
        voiced = f0 > 0
        jump = (step_semi > JUMP_SEMITONES) & voiced[1:] & voiced[:-1]
        idx = np.where(jump)[0]
        if idx.size == 0:
            return audio, 0

        groups = []
        for k in idx:
            if groups and (k - groups[-1][1]) * 0.02 < 0.8:
                groups[-1][1] = int(k)
            else:
                groups.append([int(k), int(k)])
        groups = groups[:8]

        out = np.asarray(audio, dtype=np.float32).copy()
        n = out.size
        used = 0
        for a, b in groups:
            s = max(0, int((a / 50.0 - 0.4) * sr))
            e = min(n, int((b / 50.0 + 0.4) * sr))
            if e - s < int(0.3 * sr):
                continue
            seg_wav = target_wav[:, s:e]
            f0a = max(0, int(a - 0.4 * 50))
            f0b = min(f0.size, int(b + 0.4 * 50))
            seg_f0 = f0[f0a:f0b]
            if seg_f0.size < 3:
                continue
            seg_audio, _ = self.model_infer(
                prompt_wav, prompt_f0, seg_wav, seg_f0,
                cfg=max(1.8, float(cfg) - 0.7), n_steps=int(n_steps) + 16,
                pitch_shift=pitch_shift,
            )
            length = e - s
            seg_audio = np.pad(seg_audio, (0, max(0, length - seg_audio.size)))[:length]
            ref = float(np.sqrt(np.mean(out[s:e] ** 2)) + 1e-8)
            cur = float(np.sqrt(np.mean(seg_audio ** 2)) + 1e-8)
            seg_audio = seg_audio * min(3.0, ref / cur)

            fade = max(1, int(0.06 * sr))
            win = np.ones(length, dtype=np.float32)
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            win[:fade] = ramp
            win[-fade:] = ramp[::-1]
            out[s:e] = out[s:e] * (1.0 - win) + seg_audio * win
            used += 1
        return out, used

    def model_infer(self, prompt_wav, prompt_f0, seg_wav, seg_f0,
                    cfg: float, n_steps: int, pitch_shift: int = 0):
        return self.svc.convert(
            prompt_wav, prompt_f0, seg_wav, np.asarray(seg_f0, dtype=np.float32),
            pitch_shift=pitch_shift, auto_shift=False, n_steps=n_steps, cfg=cfg,
        )

    def probe_comfort(self, prompt_wav, prompt_f0, target_wav, target_f0,
                      pitch_shift: int = 0) -> dict:
        """Sing ~6 s of the hardest phrase and measure how far the pitch drifts.

        Cheap way to answer "can this voice handle this song at all?" before
        spending a full generation.
        """
        sr = self.svc.sr
        f0 = np.asarray(target_f0, dtype=np.float32)
        semis = np.abs(np.diff(np.log2(np.maximum(f0, 1e-6)))) * 12.0
        win = 300  # 6 s at 50 fps
        start = 0
        if semis.size > win:
            cs = np.concatenate([[0.0], np.cumsum(semis > 4)])
            sums = cs[win:] - cs[:-win]
            start = int(np.argmax(sums))
        a = int(start / 50.0 * sr)
        b = min(int((start + win) / 50.0 * sr), target_wav.shape[1])
        seg_wav = target_wav[:, a:b]
        seg_f0 = f0[start:start + win]
        voiced = f0[f0 > 0]
        p95 = float(np.percentile(voiced, 95)) if voiced.size else 0.0
        top = float(voiced.max()) if voiced.size else 0.0
        if p95 > GATE_P95 or top > GATE_MAX:
            # clearly above the reference voice's range: refuse before spending GPU time
            return {"too_hard": True, "pitch_err": 0.0, "bad_ratio": 1.0, "p95": p95}
        if seg_f0.size < 20 or b - a < int(1.0 * sr):
            return {"too_hard": False, "pitch_err": 0.0, "bad_ratio": 0.0, "p95": p95}

        audio, _ = self.model_infer(prompt_wav, prompt_f0, seg_wav, seg_f0,
                                    cfg=2.5, n_steps=24, pitch_shift=pitch_shift)
        tmp = os.path.join(WORK_DIR, "probe.wav")
        sf.write(tmp, np.clip(audio, -1, 1), sr)
        out_f0 = self.f0(tmp)
        n = min(out_f0.size, seg_f0.size)
        o, t = out_f0[:n], seg_f0[:n]
        mask = (o > 0) & (t > 0)
        if int(mask.sum()) < 20:
            return {"too_hard": True, "pitch_err": 9.0, "bad_ratio": 1.0, "p95": p95}
        err = np.abs(np.log2(o[mask] / t[mask]) * 12.0 - float(pitch_shift))
        pitch_err = float(np.median(err))
        bad_ratio = float(np.mean(err > 2.0))
        too_hard = pitch_err > GATE_PITCH_ERR or bad_ratio > GATE_BAD_RATIO
        return {"too_hard": bool(too_hard), "pitch_err": pitch_err,
                "bad_ratio": bad_ratio, "p95": p95}

    def run(self, source: str, profile_id: str, pitch_shift: Optional[int] = None,
            auto_shift: Optional[bool] = None, n_steps: Optional[int] = None, cfg: Optional[float] = None,
            mix_accompaniment: Optional[bool] = None, vocal_gain_db: float = 0.0,
            full_song: Optional[bool] = None, lift_ratio: Optional[float] = None, progress=None) -> str:
        apply_settings()
        if pitch_shift is None:
            pitch_shift = DEFAULT_PITCH
        if n_steps is None:
            n_steps = N_STEPS_DEFAULT
        if cfg is None:
            cfg = DEFAULT_CFG
        if mix_accompaniment is None:
            mix_accompaniment = not ACAPPELLA
        if auto_shift is None:
            auto_shift = AUTO_SHIFT
        if full_song is None:
            full_song = FULL_SONG
        if lift_ratio is None:
            lift_ratio = LIFT_RATIO

        # reproducible runs: every job reseeds so the same seed gives the same take
        job_seed = random.randint(1, 2 ** 31 - 1) if RANDOM_SEED else SEED
        random.seed(job_seed)
        np.random.seed(job_seed % (2 ** 32))
        try:
            torch.manual_seed(job_seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(job_seed)
        except Exception:
            pass

        job = uuid.uuid4().hex[:12]
        jdir = os.path.join(WORK_DIR, job)
        os.makedirs(jdir, exist_ok=True)
        t0 = time.time()

        def step(pct: float, desc: str):
            log(f"[{job}] {desc}")
            set_job_state(desc, job, t0)
            if progress is not None:
                try:
                    progress(pct, desc=desc)
                except Exception:
                    pass

        step(0.02, "prepare audio")
        src_wav = to_wav24k(source, os.path.join(jdir, "input.wav"))

        step(0.08, "separate vocals / accompaniment")
        vocals, acc, sep_sr = self.sep.separate(src_wav)
        if vocals.ndim == 2:
            vocals = vocals.mean(0)
        if acc.ndim == 2:
            acc = acc.mean(0)

        if not full_song:
            _clip = random.uniform(CLIP_MIN, max(CLIP_MIN, CLIP_MAX))
            start, end = excerpt_bounds(vocals, sep_sr, _clip)
            log(f"[{job}] clip length {_clip:.1f}s")
            vocals = vocals[start:end]
            acc = acc[start:end]
            log(f"[{job}] excerpt {start / sep_sr:.1f}-{end / sep_sr:.1f}s")

        vocals_path = os.path.join(jdir, "vocals.wav")
        sf.write(vocals_path, vocals, sep_sr)
        acc_path = os.path.join(jdir, "acc.wav")
        sf.write(acc_path, acc, sep_sr)
        dur = len(vocals) / sep_sr
        log(f"[{job}] vocals {_fmt_dur(dur)}")

        step(0.2, "extract target f0")
        target_f0 = polish.smooth_f0(self.f0(vocals_path))
        target_wav = load_audio(vocals_path, self.svc.sr)

        prompt_wav, prompt_f0 = self.profile(profile_id)

        if AUTO_TRANSPOSE and not auto_shift and pitch_shift == 0:
            voiced = target_f0[target_f0 > 0]
            if voiced.size:
                p95 = float(np.percentile(voiced, 95))
                if p95 > HIGH_F0_LIMIT:
                    pitch_shift = -2 if p95 > HIGH_F0_LIMIT * 1.15 else -1
                    log(f"[{job}] auto transpose {pitch_shift} semitones (p95 f0={p95:.0f}Hz)")

        prompt_wav, prompt_f0 = self.profile(profile_id)

        # Male (or otherwise low) sources: lift them into the reference voice's
        # range.  Never shift down - that is what made female songs sound male.
        if pitch_shift == 0 and not auto_shift:
            src_v = target_f0[target_f0 > 0]
            prm_v = prompt_f0[prompt_f0 > 0]
            if src_v.size > 50 and prm_v.size > 50:
                s_med = float(np.median(src_v))
                p_med = float(np.median(prm_v))
                if s_med < lift_ratio * p_med:
                    lift = int(round(12.0 * np.log2(p_med / s_med)))
                    if LIFT_MAX <= 0:
                        pitch_shift = 0
                    else:
                        pitch_shift = max(1, min(LIFT_MAX, lift))
                    log(f"[{job}] low source compensated: +{pitch_shift} semitones "
                        f"(src {s_med:.0f}Hz vs ref {p_med:.0f}Hz)")

        step(0.3, "zero-shot timbre conversion")
        audio, shift = self.svc.convert(
            prompt_wav, prompt_f0, target_wav, target_f0,
            pitch_shift=pitch_shift, auto_shift=auto_shift,
            n_steps=n_steps or N_STEPS_DEFAULT, cfg=cfg,
        )
        log(f"[{job}] svc done shift={shift} in {time.time() - t0:.0f}s")

        if REPAIR_JUMPS:
            try:
                audio, repaired = self._repair_jumps(
                    audio, prompt_wav, prompt_f0, target_wav, target_f0,
                    cfg=cfg, n_steps=n_steps or N_STEPS_DEFAULT,
                    pitch_shift=pitch_shift,
                )
                log(f"[{job}] repaired {repaired} jump windows")
            except Exception as exc:
                log(f"[{job}] jump repair skipped: {exc}")

        if POLISH:
            try:
                orig = target_wav.squeeze(0).detach().cpu().numpy().astype(np.float32)
                audio = polish.polish(audio, orig, target_f0,
                                      consonant_mix=CONSONANT_MIX, deess_db=DEESS_DB,
                                      comp_thr=COMP_THR, comp_ratio=COMP_RATIO,
                                      ceiling_db=LIMIT_DB,
                                      denoise_strength=(DENOISE if DENOISE_ON else 0.0),
                                      limit_on=LIMIT_ON)
                log(f"[{job}] polished consonants={CONSONANT_MIX} deess={DEESS_DB}dB "
                    f"comp={COMP_THR}dB/{COMP_RATIO}:1 limit={LIMIT_DB if LIMIT_ON else 'off'}dB "
                    f"denoise={DENOISE if DENOISE_ON else 'off'}")
            except Exception as exc:
                log(f"[{job}] polish skipped: {exc}")

        vocal_out = os.path.join(jdir, "vocal_out.wav")
        sf.write(vocal_out, np.clip(audio, -1, 1), self.svc.sr)

        step(0.9, "mix and export")
        final_wav = os.path.join(jdir, "cover.wav")
        if mix_accompaniment:
            import librosa

            acc24, _ = librosa.load(acc_path, sr=self.svc.sr, mono=True)
            n = min(len(acc24), len(audio))
            voc = audio[:n]
            if vocal_gain_db:
                voc = voc * (10 ** (vocal_gain_db / 20))
            mix = voc + acc24[:n]
            peak = float(np.max(np.abs(mix)) or 1.0)
            if peak > 0.98:
                mix = mix / peak * 0.98
            sf.write(final_wav, mix, self.svc.sr)
        else:
            shutil.copyfile(vocal_out, final_wav)

        out_mp3 = os.path.join(jdir, f"cover_{job}.mp3")
        to_mp3(final_wav, out_mp3)
        step(1.0, "done")
        _release(keep=jdir)
        set_job_state("idle", job, t0)
        return out_mp3










