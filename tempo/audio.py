"""Audio assembly: download SFX, build a mix timeline, write final WAV."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

GUNSHOT_URL = "https://www.youtube.com/watch?v=QgZ3jg1cAGc"


@dataclass
class AudioEvent:
    t: float          # seconds
    kind: str         # "shot" | "mate" | "click"


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def ensure_gunshot(assets_dir: Path) -> Path:
    """Download + trim + normalize the gunshot to assets/gunshot.wav.

    Idempotent: if the file already exists, return it.
    """
    out = assets_dir / "gunshot.wav"
    if out.exists():
        return out

    assets_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        raw_template = str(td_path / "raw.%(ext)s")
        # yt-dlp: bestaudio, no postproc — let ffmpeg handle the conversion.
        ydl = shutil.which("yt-dlp")
        if not ydl:
            raise RuntimeError("yt-dlp not found on PATH")
        proc = _run([
            ydl, "-x", "--audio-format", "wav",
            "-o", raw_template, GUNSHOT_URL,
        ], check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                "yt-dlp failed for gunshot URL.\n"
                f"stderr:\n{proc.stderr}\n"
                "If YouTube is blocking, manually drop a gunshot WAV at "
                f"{out} and re-run."
            )

        # Find the downloaded raw file.
        raws = list(td_path.glob("raw.*"))
        if not raws:
            raise RuntimeError("yt-dlp produced no output file")
        raw = raws[0]

        # Strip leading silence, take ~0.6s, loudnorm. silenceremove start_periods=1
        # chops the lead-in; atrim then bounds the duration.
        ff = shutil.which("ffmpeg") or "ffmpeg"
        _run([
            ff, "-y", "-i", str(raw),
            "-af",
            "silenceremove=start_periods=1:start_threshold=-30dB:start_silence=0.05,"
            "atrim=0:0.6,asetpts=PTS-STARTPTS,"
            "afade=t=out:st=0.5:d=0.1,"
            "loudnorm=I=-12:TP=-1.0:LRA=9",
            "-ar", "48000", "-ac", "2",
            str(out),
        ])
    return out


def ensure_click(assets_dir: Path) -> Path:
    """Synthesize a short quiet click for non-attacking moves."""
    out = assets_dir / "click.wav"
    if out.exists():
        return out
    assets_dir.mkdir(parents=True, exist_ok=True)
    ff = shutil.which("ffmpeg") or "ffmpeg"
    _run([
        ff, "-y", "-f", "lavfi", "-i", "sine=frequency=1400:duration=0.05",
        "-af", "afade=t=out:st=0.02:d=0.03,volume=-22dB",
        "-ar", "48000", "-ac", "2",
        str(out),
    ])
    return out


def build_audio_track(
    *,
    events: list[AudioEvent],
    total_duration: float,
    gunshot: Path,
    click: Path,
    out_path: Path,
) -> None:
    """Build a mixed WAV at out_path using adelay + amix."""
    if not events:
        # Generate a silent track.
        ff = shutil.which("ffmpeg") or "ffmpeg"
        _run([
            ff, "-y", "-f", "lavfi",
            "-i", f"anullsrc=r=48000:cl=stereo:d={total_duration:.3f}",
            "-c:a", "pcm_s16le", str(out_path),
        ])
        return

    inputs: list[str] = []
    filter_parts: list[str] = []
    mix_labels: list[str] = []

    for i, ev in enumerate(events):
        src = gunshot if ev.kind in ("shot", "mate") else click
        inputs += ["-i", str(src)]
        delay_ms = int(ev.t * 1000)
        # Volume per event kind:
        if ev.kind == "mate":
            # The checkmate shot also gets an echo tail.
            chain = (
                f"[{i}:a]volume=1.6,"
                f"adelay={delay_ms}|{delay_ms},"
                f"aecho=0.6:0.5:80|160:0.5|0.3"
                f"[a{i}]"
            )
        elif ev.kind == "shot":
            chain = f"[{i}:a]volume=1.0,adelay={delay_ms}|{delay_ms}[a{i}]"
        else:  # click
            chain = f"[{i}:a]volume=0.5,adelay={delay_ms}|{delay_ms}[a{i}]"
        filter_parts.append(chain)
        mix_labels.append(f"[a{i}]")

    n = len(events)
    mix = (
        "".join(mix_labels)
        + f"amix=inputs={n}:normalize=0:dropout_transition=0[mix];"
        + f"[mix]apad,atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
        + "aformat=sample_fmts=s16:channel_layouts=stereo[out]"
    )
    filter_graph = ";".join(filter_parts) + ";" + mix

    ff = shutil.which("ffmpeg") or "ffmpeg"
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
            f"cmd: {' '.join(cmd[:6])} ... ({len(inputs)//2} inputs)\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )


def mux_video(
    *,
    frames_dir: Path,
    audio_path: Path,
    out_path: Path,
    fps_in: int = 1,
    fps_out: int = 30,
) -> None:
    """Stitch frame_%03d.png at fps_in, encode 30fps + AAC, mux."""
    ff = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ff, "-y",
        "-framerate", str(fps_in),
        "-start_number", "0",
        "-i", str(frames_dir / "frame_%03d.png"),
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(fps_out),
        "-vf", "fps={fps},format=yuv420p".format(fps=fps_out),
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = _run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mux failed.\nstderr:\n{proc.stderr[-2000:]}"
        )
