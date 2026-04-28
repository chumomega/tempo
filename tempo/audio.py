"""Audio assembly: download SFX, build a layered timeline, write final WAV."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Source URLs.
GUNSHOT_URL = "https://www.youtube.com/watch?v=QgZ3jg1cAGc"
FAHH_URL = "https://www.youtube.com/watch?v=-vH2b55TSHI"
BITE_URL = "https://www.youtube.com/watch?v=LwJBAVZKWYU"
CHESS_SFX_URL = "https://www.youtube.com/watch?v=7skwR49UhqA"  # multi-SFX

# Trim windows (start, duration) inside CHESS_SFX_URL.
# The video plays "move" at ~2s and "castling" at ~4s. We don't pull capture
# from this video any more — captures use the dedicated bite SFX.
CHESS_SFX_WINDOWS = {
    "thud":     (1.85, 0.55),
    "castling": (3.85, 0.80),
}


@dataclass(frozen=True)
class Layer:
    """One SFX layer within a ply: relative offset (s) inside the move
    window, and which sound file to play."""
    offset: float
    kind: str   # "thud" | "castling" | "crunch" | "gunshot" | "fahh" | "mate"


@dataclass
class PlyAudio:
    """All layered SFX events for a single ply, plus the wall-clock start
    time of the ply."""
    t_start: float
    layers: list[Layer]


# ---------- low-level helpers ----------

def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _which(tool: str) -> str:
    p = shutil.which(tool)
    if not p:
        raise RuntimeError(f"{tool} not found on PATH")
    return p


def _yt_dlp_download(url: str, td: Path) -> Path:
    ydl = _which("yt-dlp")
    template = str(td / "raw.%(ext)s")
    proc = _run([ydl, "-x", "--audio-format", "wav", "-o", template, url],
                check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed for {url}\nstderr:\n{proc.stderr}"
        )
    raws = list(td.glob("raw.*"))
    if not raws:
        raise RuntimeError(f"yt-dlp produced no output for {url}")
    return raws[0]


def _ffmpeg_normalize(src: Path, *, dst: Path,
                      start: float = 0.0, duration: float | None = None,
                      strip_silence: bool = False,
                      target_lufs: float = -12.0) -> None:
    """Cut [start, start+duration) from src, optionally chop leading silence,
    fade out, loudnorm, save as 48k stereo s16 WAV."""
    ff = _which("ffmpeg")
    af_parts: list[str] = []
    if strip_silence:
        af_parts.append(
            "silenceremove=start_periods=1:start_threshold=-30dB:start_silence=0.05"
        )
    if duration is not None:
        af_parts.append(f"atrim=0:{duration:.3f}")
    af_parts.append("asetpts=PTS-STARTPTS")
    if duration is not None and duration > 0.15:
        fade_start = max(0.0, duration - 0.10)
        af_parts.append(f"afade=t=out:st={fade_start:.3f}:d=0.10")
    af_parts.append(f"loudnorm=I={target_lufs}:TP=-1.0:LRA=9")
    af = ",".join(af_parts)
    cmd = [ff, "-y", "-ss", f"{start:.3f}", "-i", str(src),
           "-af", af, "-ar", "48000", "-ac", "2", str(dst)]
    proc = _run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg trim/normalize failed:\n{proc.stderr[-1500:]}")


# ---------- per-SFX preparers ----------

def ensure_gunshot(assets_dir: Path) -> Path:
    out = assets_dir / "gunshot.wav"
    if out.exists():
        return out
    assets_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        raw = _yt_dlp_download(GUNSHOT_URL, td_path)
        _ffmpeg_normalize(raw, dst=out, duration=0.6, strip_silence=True,
                          target_lufs=-12.0)
    return out


def ensure_bite(assets_dir: Path) -> Path:
    out = assets_dir / "bite.wav"
    if out.exists():
        return out
    assets_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        raw = _yt_dlp_download(BITE_URL, td_path)
        # Strip leading silence, take first ~0.7s of the bite.
        _ffmpeg_normalize(raw, dst=out, duration=0.7, strip_silence=True,
                          target_lufs=-12.0)
    return out


def ensure_fahh(assets_dir: Path) -> Path:
    out = assets_dir / "fahh.wav"
    if out.exists():
        return out
    assets_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        raw = _yt_dlp_download(FAHH_URL, td_path)
        # Strip leading silence, take first ~0.9s of the actual sound.
        _ffmpeg_normalize(raw, dst=out, duration=0.9, strip_silence=True,
                          target_lufs=-14.0)
    return out


def ensure_chess_sfx(assets_dir: Path) -> dict[str, Path]:
    """Download the multi-SFX clip once and slice out thud / castling / crunch."""
    outs = {k: assets_dir / f"{k}.wav" for k in CHESS_SFX_WINDOWS}
    if all(p.exists() for p in outs.values()):
        return outs
    assets_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        raw = _yt_dlp_download(CHESS_SFX_URL, td_path)
        for kind, (start, dur) in CHESS_SFX_WINDOWS.items():
            target_lufs = -16.0 if kind == "thud" else -14.0
            _ffmpeg_normalize(raw, dst=outs[kind],
                              start=start, duration=dur,
                              strip_silence=False,
                              target_lufs=target_lufs)
    return outs


def ensure_all_sfx(assets_dir: Path) -> dict[str, Path]:
    paths = ensure_chess_sfx(assets_dir)
    paths["gunshot"] = ensure_gunshot(assets_dir)
    paths["fahh"] = ensure_fahh(assets_dir)
    paths["bite"] = ensure_bite(assets_dir)
    return paths


def sfx_duration(path: Path) -> float:
    """Probe the duration of a WAV file."""
    ff = _which("ffprobe")
    proc = _run([ff, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(proc.stdout.strip())


# ---------- timeline build ----------

def build_audio_track(
    *,
    plies: list[PlyAudio],
    total_duration: float,
    sfx_paths: dict[str, Path],
    out_path: Path,
) -> None:
    """Lay every layer at its absolute timestamp; mix into a single track."""
    # Per-layer volume / treatment. Mate layer is gunshot + extra reverb.
    treatments = {
        "thud":     ("thud",     "volume=0.8"),
        "castling": ("castling", "volume=0.95"),
        "bite":     ("bite",     "volume=1.05"),
        "gunshot":  ("gunshot",  "volume=1.0"),
        "fahh":     ("fahh",     "volume=1.1"),
        # Mate: fahh, pitched down ~3 semitones (which also slows it ~17%),
        # then a long decaying reverb tail (3 echoes out to ~400ms).
        "mate":     ("fahh",
                     "volume=1.3,"
                     "asetrate=48000*0.82,aresample=48000,"
                     "aecho=0.75:0.6:140|260|420:0.6|0.4|0.25"),
    }

    inputs: list[str] = []
    chains: list[str] = []
    mix_labels: list[str] = []

    idx = 0
    for ply in plies:
        for layer in ply.layers:
            sfx_name, treatment = treatments[layer.kind]
            src = sfx_paths[sfx_name]
            inputs += ["-i", str(src)]
            t_abs_ms = int(round((ply.t_start + layer.offset) * 1000))
            chains.append(
                f"[{idx}:a]{treatment},adelay={t_abs_ms}|{t_abs_ms}[a{idx}]"
            )
            mix_labels.append(f"[a{idx}]")
            idx += 1

    if not inputs:
        ff = _which("ffmpeg")
        _run([ff, "-y", "-f", "lavfi",
              "-i", f"anullsrc=r=48000:cl=stereo:d={total_duration:.3f}",
              "-c:a", "pcm_s16le", str(out_path)])
        return

    n = len(mix_labels)
    mix_tail = (
        "".join(mix_labels)
        + f"amix=inputs={n}:normalize=0:dropout_transition=0[mix];"
        + f"[mix]apad,atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
        + "aformat=sample_fmts=s16:channel_layouts=stereo[out]"
    )
    filter_graph = ";".join(chains) + ";" + mix_tail

    ff = _which("ffmpeg")
    cmd = [ff, "-y", *inputs,
           "-filter_complex", filter_graph,
           "-map", "[out]",
           "-ar", "48000", "-ac", "2",
           "-c:a", "pcm_s16le",
           str(out_path)]
    proc = _run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg audio mix failed.\n"
            f"({len(inputs)//2} inputs, {n} chains)\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )


# ---------- video stitch ----------

def mux_video_concat(
    *,
    frames_dir: Path,
    durations: list[float],
    audio_path: Path,
    out_path: Path,
    fps_out: int = 30,
) -> None:
    """Stitch frames with per-frame durations via ffmpeg's concat demuxer.

    durations[i] is the on-screen time for frame_i.png. There are len(durations)
    frames named frame_000.png .. frame_(N-1).png.
    """
    concat_path = frames_dir / "concat.txt"
    lines: list[str] = ["ffconcat version 1.0"]
    n = len(durations)
    for i, d in enumerate(durations):
        lines.append(f"file 'frame_{i:03d}.png'")
        lines.append(f"duration {d:.3f}")
    # The concat demuxer ignores the duration of the *last* file — repeat it
    # so the last real frame's duration is honored.
    lines.append(f"file 'frame_{n-1:03d}.png'")
    concat_path.write_text("\n".join(lines) + "\n")

    ff = _which("ffmpeg")
    cmd = [
        ff, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-i", str(audio_path),
        "-vsync", "cfr",
        "-r", str(fps_out),
        "-vf", f"fps={fps_out},format=yuv420p",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = _run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed.\nstderr:\n{proc.stderr[-2000:]}")
