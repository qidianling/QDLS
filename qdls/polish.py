"""Light post-processing for SVC output: consonant guard, de-harsh EQ, pitch hygiene."""
from __future__ import annotations

import numpy as np

SR = 24000
HOP = 480


def smooth_f0(f0: np.ndarray, median: int = 5, min_voiced_frames: int = 3) -> np.ndarray:
    """Kill RMVPE spikes and one-frame voiced blips that turn into rasp/squeaks."""
    f0 = np.asarray(f0, dtype=np.float32).copy()
    if f0.size == 0:
        return f0

    # median filter over voiced values only, so silence does not drag pitch down
    if median >= 3:
        pad = median // 2
        ext = np.pad(f0, (pad, pad), mode="edge")
        out = np.empty_like(f0)
        for i in range(f0.size):
            win = ext[i:i + median]
            voiced = win[win > 0]
            out[i] = np.median(voiced) if voiced.size >= 2 else f0[i]
        f0 = out

    # drop very short voiced islands (consonant noise usually lasts 1-2 frames)
    voiced = f0 > 0
    i = 0
    while i < f0.size:
        if not voiced[i]:
            i += 1
            continue
        j = i
        while j < f0.size and voiced[j]:
            j += 1
        if (j - i) < min_voiced_frames:
            f0[i:j] = 0.0
        i = j

    return fix_octaves(f0)


def fix_octaves(f0: np.ndarray, win: int = 9, min_jump: float = 0.25,
                tol: float = 0.12, slew: float = 0.5,
                max_spike_frames: int = 4) -> np.ndarray:
    """Fold brief octave spikes and limit absurd pitch slew.

    Descending phrases are where RMVPE drops or duplicates an octave; the model
    then receives an impossible jump and cracks.  Sustained octave leaps are real
    melody notes and are left alone (only runs <= max_spike_frames are folded).
    """
    f0 = np.asarray(f0, dtype=np.float32).copy()
    idx = np.where(f0 > 0)[0]
    if idx.size < 3:
        return f0
    vals = np.log2(f0[idx].astype(np.float64))

    local = np.empty_like(vals)
    half = max(1, win // 2)
    for k in range(vals.size):
        seg = vals[max(0, k - half):k + half + 1]
        local[k] = np.median(seg)

    diff = vals - local
    nearest = np.round(diff)
    foldable = (np.abs(diff) > min_jump) & (np.abs(diff - nearest) < tol)

    i = 0
    while i < foldable.size:
        if not foldable[i]:
            i += 1
            continue
        j = i
        while j < foldable.size and foldable[j]:
            j += 1
        if (j - i) <= max_spike_frames:
            vals[i:j] = vals[i:j] - nearest[i:j]
        i = j

    # slew limit: about 1 semitone per 20 ms frame
    limited = vals.copy()
    for k in range(1, vals.size):
        delta = limited[k] - limited[k - 1]
        if abs(delta) > slew:
            limited[k] = limited[k - 1] + np.sign(delta) * slew
    f0[idx] = (2.0 ** limited).astype(np.float32)
    return f0


def _frame_mask(f0: np.ndarray, n: int, hop: int = HOP) -> np.ndarray:
    if f0.size == 0:
        return np.zeros(n, dtype=np.float32)
    centers = np.arange(f0.size) * hop + hop * 0.5
    m = np.interp(np.arange(n), centers, (f0 > 0).astype(np.float32))
    return m.astype(np.float32)


def _smooth(x: np.ndarray, ms: float = 8.0, sr: int = SR) -> np.ndarray:
    win = max(1, int(ms / 1000.0 * sr))
    if win <= 1:
        return x
    kernel = np.hanning(win)
    kernel /= kernel.sum()
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def soften_jumps(f0: np.ndarray, thresh_semi: float = 4.0, span: int = 2,
                 iters: int = 2) -> np.ndarray:
    """Turn instant pitch steps into a 40-80 ms glide.

    A singer cannot jump 8 semitones inside one 20 ms frame; the SVC model gets an
    impossible f0 discontinuity and cracks.  Only frames around such jumps are
    touched, so held notes, vibrato and slow glides stay intact.
    """
    f0 = np.asarray(f0, dtype=np.float32).copy()
    idx = np.where(f0 > 0)[0]
    if idx.size < 5:
        return f0
    vals = np.log2(f0[idx].astype(np.float64))
    semis = np.diff(vals) * 12.0
    jumps = np.where(np.abs(semis) > thresh_semi)[0]
    if jumps.size == 0:
        return f0

    mask = np.zeros(vals.size, dtype=bool)
    for k in jumps:
        mask[max(0, k - span):min(vals.size, k + span + 2)] = True

    kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    kernel /= kernel.sum()
    for _ in range(iters):
        src = vals.copy()
        for k in np.where(mask)[0]:
            lo, hi = max(0, k - 2), min(vals.size, k + 3)
            seg = src[lo:hi]
            ker = kernel[2 - (k - lo):2 + (hi - k)]
            ker = ker / ker.sum()
            vals[k] = float(np.dot(seg, ker))

    f0[idx] = (2.0 ** vals).astype(np.float32)
    return f0


def consonant_guard(gen: np.ndarray, orig: np.ndarray, f0: np.ndarray,
                    mix: float = 0.5, floor: float = 0.004,
                    breath_mix: float = 0.6) -> np.ndarray:
    """Fill only the holes where the model dropped an unvoiced consonant/breath."""
    n = max(gen.size, orig.size)
    gen = np.pad(gen.astype(np.float32), (0, n - gen.size))
    orig = np.pad(orig.astype(np.float32), (0, n - orig.size))
    voiced = _smooth(_frame_mask(f0, n), ms=10.0)

    orig_env = _smooth(np.abs(orig), ms=12.0)
    gen_env = _smooth(np.abs(gen), ms=12.0)
    orig_gate = np.clip(orig_env / max(floor, 1e-6), 0.0, 1.0)

    typical = float(np.percentile(gen_env[gen_env > 0], 90)) if np.any(gen_env > 0) else 0.0
    if typical <= 0:
        typical = float(np.max(gen_env) or 1.0)
    hole = np.clip(1.0 - gen_env / (0.12 * typical), 0.0, 1.0)

    # smooth the switch: an instant 0->0.5 gain step is itself an audible click
    keep = _smooth((1.0 - voiced) * orig_gate * hole, ms=25.0) * mix

    # breaths: unvoiced stretches the model renders near-silent.  Borrow more of
    # the source there so phrases do not sound like they never breathe.
    breath = (1.0 - voiced) * orig_gate * np.clip(1.0 - gen_env / (0.06 * typical), 0.0, 1.0)
    breath = _smooth(breath, ms=40.0) * breath_mix
    keep = np.maximum(keep, breath)
    return (gen * (1.0 - keep) + orig * keep).astype(np.float32)


def shelf(x: np.ndarray, sr: int = SR, cutoff: float = 6500.0,
          gain_db: float = -3.0, order: float = 4.0) -> np.ndarray:
    """Smooth high shelf; negative gain takes the edge/sandiness off."""
    if x.size == 0 or gain_db == 0:
        return x
    n = x.size
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    g = 1.0 / (1.0 + (freqs / cutoff) ** order)
    gain = 10.0 ** (gain_db / 20.0)
    return np.fft.irfft(spec * (1.0 - g * (1.0 - gain)), n).astype(np.float32)


def lowpass(x: np.ndarray, sr: int = SR, cutoff: float = 15500.0) -> np.ndarray:
    n = x.size
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    g = 1.0 / (1.0 + (freqs / cutoff) ** 6)
    return np.fft.irfft(spec * g, n).astype(np.float32)


def compress(x: np.ndarray, sr: int = SR, threshold_db: float = -18.0,
             ratio: float = 2.5, release_ms: float = 90.0) -> np.ndarray:
    """Gentle downward compression so transients stop poking the limiter."""
    if x.size == 0:
        return x
    env = _smooth(np.abs(x), ms=6.0, sr=sr)
    thr = 10.0 ** (threshold_db / 20.0)
    gain = np.ones_like(env)
    over = env > thr
    if np.any(over):
        gain[over] = (thr / env[over]) ** (1.0 - 1.0 / ratio)
    gain = _smooth(gain, ms=release_ms, sr=sr)
    return (x * gain).astype(np.float32)


def limiter(x: np.ndarray, sr: int = SR, ceiling_db: float = -1.5,
            lookahead_ms: float = 4.0) -> np.ndarray:
    """Look-ahead peak limiter: transparent unless a transient would clip."""
    if x.size == 0:
        return x
    ceiling = 10.0 ** (ceiling_db / 20.0)
    try:
        from scipy.ndimage import maximum_filter1d

        width = max(1, int(lookahead_ms / 1000.0 * sr))
        env = maximum_filter1d(np.abs(x), size=width, mode="nearest")
    except Exception:
        env = np.abs(x)
    gain = np.minimum(1.0, ceiling / np.maximum(env, 1e-6)).astype(np.float32)
    gain = _smooth(gain, ms=3.0, sr=sr)
    return (x * gain).astype(np.float32)


def soft_limit(x: np.ndarray, ceiling: float = 0.95) -> np.ndarray:
    """Plain peak normalisation; tanh here only added odd harmonics (sandiness)."""
    peak = float(np.max(np.abs(x)) or 0.0)
    if peak <= 0:
        return x
    if peak > ceiling:
        x = x * (ceiling / peak)
    return x.astype(np.float32)


def denoise(x: np.ndarray, sr: int = SR, strength: float = 1.0) -> np.ndarray:
    """Rumble filter + soft expander: cleans hiss between phrases without touching
    the voice.  Aggressive spectral subtraction audibly hollows out the tone, so
    this stays conservative on purpose.
    """
    if x.size == 0 or strength <= 0:
        return x

    # 1) high-pass at 70 Hz (removes rumble / handling noise)
    n = x.size
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    hp = 1.0 - 1.0 / (1.0 + (freqs / 70.0) ** 4)
    y = np.fft.irfft(spec * hp, n).astype(np.float32)

    # 2) soft expander: below -42 dBFS pull the signal down
    env = _smooth(np.abs(y), ms=10.0, sr=sr)
    thr = 10.0 ** (-42.0 / 20.0)
    floor_gain = max(0.05, 1.0 - 0.9 * float(strength))
    target = np.where(env < thr, floor_gain, 1.0).astype(np.float32)
    gain = _smooth(target, ms=45.0, sr=sr)
    return (y * gain).astype(np.float32)


def polish(gen: np.ndarray, orig: np.ndarray, f0: np.ndarray,
           consonant_mix: float = 0.25, deess_db: float = -3.5,
           compress_on: bool = True, comp_thr: float = -18.0,
           comp_ratio: float = 2.5, ceiling_db: float = -1.5,
           denoise_strength: float = 1.0, limit_on: bool = True) -> np.ndarray:
    y = consonant_guard(gen, orig, f0, mix=consonant_mix)
    y = denoise(y, strength=denoise_strength)
    y = shelf(y, gain_db=deess_db)
    y = lowpass(y)
    if compress_on:
        y = compress(y, threshold_db=comp_thr, ratio=comp_ratio)
        peak = float(np.max(np.abs(y)) or 0.0)
        if peak > 0:
            y = y * (0.85 / peak)
    if limit_on:
        y = limiter(y, ceiling_db=ceiling_db)
    y = soft_limit(y, ceiling=0.92)
    return y.astype(np.float32)


