"""CLI: PGN -> 1080x1920 MP4 with one-sound-per-ply and a piece-slide animation."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import chess

from .audio import (
    Layer,
    PlyAudio,
    build_audio_track,
    ensure_all_sfx,
    mux_video_concat,
)
from .engine import PlyState, iter_plies, load_game
from .render import Fonts, render_animation_frame, render_frame


# Per-ply on-screen durations (seconds). Mate gets the longest hold; checks
# linger so the fahh has room to breathe.
DUR_BASE = 1.3
DUR_CAPTURE = 1.8
DUR_CHECK = 2.5
DUR_MATE = 4.0
DUR_START = 1.3   # how long the starting position holds before move 1

# Piece-slide animation. Each ply renders SLIDE_STEPS in-flight frames at
# SLIDE_DUR / SLIDE_STEPS seconds each, followed by the post-move frame.
SLIDE_STEPS = 5
SLIDE_DUR = 0.30


def _print_log(state: PlyState, sfx: str) -> None:
    prefix = (f"{state.move_number}." if state.ply_index % 2 == 1
              else f"{state.move_number}...")
    label = f"{prefix} {state.san}"
    tags: list[str] = []
    if state.is_checkmate:
        tags.append("MATE")
    elif state.is_check:
        tags.append("CHECK")
    if state.is_capture and not state.is_castling:
        tags.append("CAPT")
    if state.is_castling:
        tags.append("CASTLE")
    if state.gunshot_targets:
        tags.append("THREAT")
    tag = " ".join(tags) if tags else "       "
    print(f"  ply {state.ply_index:>3}  {label:<14}  {tag:<22}  -> {sfx}")


def _ply_duration(state: PlyState) -> float:
    if state.is_checkmate:
        return DUR_MATE
    if state.is_check:
        return DUR_CHECK
    if state.is_capture or state.is_castling:
        return DUR_CAPTURE
    return DUR_BASE


def _pick_sfx(state: PlyState) -> str:
    """Pick the single SFX to fire for this ply.

    Priority (highest wins):
      castling > mate > bite (capture) > fahh (check) > gunshot (rule-B
      threat) > thud (default).
    """
    if state.is_castling:
        return "castling"
    if state.is_checkmate:
        return "mate"
    if state.is_capture:
        return "bite"
    if state.is_check:
        return "fahh"
    if state.gunshot_targets:
        return "gunshot"
    return "thud"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PGN -> vertical MP4 short")
    p.add_argument("pgn", nargs="?", default="game.pgn",
                   help="path to PGN file (default: game.pgn)")
    p.add_argument("--start-from", type=int, default=1,
                   help="render only from this full-move number onward")
    p.add_argument("--out", type=Path, default=Path("out/chess_short.mp4"),
                   help="output mp4 path")
    p.add_argument("--keep-frames", action="store_true",
                   help="keep the rendered PNG frames and intermediate audio")
    args = p.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    pgn_path = Path(args.pgn)
    if not pgn_path.is_absolute():
        pgn_path = (project_root / pgn_path).resolve()
    if not pgn_path.exists():
        print(f"PGN not found: {pgn_path}", file=sys.stderr)
        return 2

    out_path = args.out
    if not out_path.is_absolute():
        out_path = (project_root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    frames_dir = project_root / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    assets_dir = project_root / "assets"

    print(f"Loading PGN: {pgn_path}")
    meta, game = load_game(pgn_path)
    states_all = iter_plies(game)
    states = [s for s in states_all if s.move_number >= args.start_from]
    if not states:
        print("No plies after applying --start-from filter.", file=sys.stderr)
        return 2

    board = game.board()
    for s in states_all:
        if s.move_number >= args.start_from:
            break
        board.push(s.last_move)

    fonts = Fonts.build()
    print(f"Game: {meta.white} ({meta.white_elo}) vs "
          f"{meta.black} ({meta.black_elo}) — {meta.result}")
    print(f"Rendering {len(states)} plies starting from move "
          f"{args.start_from} ...")

    durations: list[float] = [DUR_START]
    render_frame(board=board, state=None, meta=meta, fonts=fonts,
                 out_path=frames_dir / "frame_000.png")

    plies_audio: list[PlyAudio] = []
    cursor = DUR_START
    next_idx = 1
    slide_step_dur = SLIDE_DUR / SLIDE_STEPS

    for state in states:
        ply_dur = _ply_duration(state)
        sfx = _pick_sfx(state)
        _print_log(state, sfx)

        # Pre-move board with the mover removed, used to render slide frames.
        moving_piece = board.piece_at(state.from_square)
        animate = (not state.is_castling) and moving_piece is not None
        if animate:
            pre_anim_board = board.copy()
            pre_anim_board.remove_piece_at(state.from_square)
            # The capturer landing on the captured piece reads better if we
            # leave the captured piece on to_sq; for en-passant the captured
            # pawn is on a different square and stays put naturally.
            for step in range(SLIDE_STEPS):
                t = (step + 1) / (SLIDE_STEPS + 1)
                render_animation_frame(
                    pre_board=pre_anim_board,
                    moving_piece=moving_piece,
                    state=state, t=t,
                    meta=meta, fonts=fonts,
                    out_path=frames_dir / f"frame_{next_idx:03d}.png",
                )
                durations.append(slide_step_dur)
                next_idx += 1

        # Apply the move and render the static post-move (arrival) frame.
        board.push(state.last_move)
        render_frame(
            board=board, state=state, meta=meta, fonts=fonts,
            out_path=frames_dir / f"frame_{next_idx:03d}.png",
        )
        post_dur = ply_dur - (SLIDE_DUR if animate else 0.0)
        durations.append(max(0.05, post_dur))
        next_idx += 1

        # SFX fires when the piece arrives — start of the post-move frame.
        sfx_offset = SLIDE_DUR if animate else 0.0
        plies_audio.append(
            PlyAudio(
                t_start=cursor + sfx_offset,
                layers=[Layer(offset=0.0, kind=sfx)],
            )
        )
        cursor += ply_dur
    total_duration = cursor

    print("Preparing audio assets ...")
    sfx_paths = ensure_all_sfx(assets_dir)

    print(f"Building audio timeline ({len(plies_audio)} sounds, "
          f"{total_duration:.1f}s total) ...")
    audio_path = project_root / "out" / "_audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    build_audio_track(
        plies=plies_audio,
        total_duration=total_duration,
        sfx_paths=sfx_paths,
        out_path=audio_path,
    )

    print(f"Muxing video -> {out_path}")
    mux_video_concat(
        frames_dir=frames_dir,
        durations=durations,
        audio_path=audio_path,
        out_path=out_path,
    )

    if not args.keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
        try:
            audio_path.unlink()
        except FileNotFoundError:
            pass

    counts: dict[str, int] = {}
    for pa in plies_audio:
        kind = pa.layers[0].kind
        counts[kind] = counts.get(kind, 0) + 1
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"Done. Wrote {out_path} ({total_duration:.1f}s, {breakdown}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
