"""CLI: PGN -> 1080x1920 MP4 with gunshot SFX on attacking moves."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import chess
import chess.pgn

from .audio import (
    AudioEvent,
    build_audio_track,
    ensure_click,
    ensure_gunshot,
    mux_video,
)
from .engine import GameMeta, PlyState, iter_plies, load_game
from .render import Fonts, render_frame


def _square_name(sq: chess.Square) -> str:
    return chess.square_name(sq)


def _print_log(state: PlyState) -> None:
    move_num = state.move_number
    prefix = f"{move_num}." if state.ply_index % 2 == 1 else f"{move_num}..."
    label = f"{prefix} {state.san}"
    if state.is_checkmate:
        tag = "MATE   "
    elif state.attacked_enemy_squares:
        tag = "ATTACK "
    else:
        tag = "       "
    attacks = [_square_name(s) for s in state.attacked_enemy_squares]
    print(f"  ply {state.ply_index:>3}  {label:<14}  {tag}  attacks={attacks}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PGN -> vertical MP4 short")
    p.add_argument("pgn", nargs="?", default="game.pgn",
                   help="path to PGN file (default: game.pgn)")
    p.add_argument("--seconds-per-move", type=float, default=1.0,
                   help="duration of each ply frame (default: 1.0)")
    p.add_argument("--start-from", type=int, default=1,
                   help="render only from this full-move number onward")
    p.add_argument("--out", type=Path, default=Path("out/chess_short.mp4"),
                   help="output mp4 path")
    p.add_argument("--no-click", action="store_true",
                   help="omit the soft click on non-attacking moves")
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

    # --start-from filters by full-move number; keep both white and black plies
    # of any retained move number.
    states = [s for s in states_all if s.move_number >= args.start_from]
    if not states:
        print("No plies after applying --start-from filter.", file=sys.stderr)
        return 2

    # Reconstruct positions for each retained ply by replaying from start.
    # We render: starting frame (the position BEFORE the first retained ply),
    # plus one frame per retained ply.
    board = game.board()
    skipped = [s for s in states_all if s.move_number < args.start_from]
    for s in skipped:
        board.push(s.last_move)

    fonts = Fonts.build()

    print(f"Game: {meta.white} ({meta.white_elo}) vs "
          f"{meta.black} ({meta.black_elo}) — {meta.result}")
    print(f"Rendering {len(states)} plies starting from move {args.start_from} ...")

    # Frame 000: the position before any retained ply (or starting position).
    render_frame(
        board=board, state=None, meta=meta, fonts=fonts,
        out_path=frames_dir / "frame_000.png",
    )

    events: list[AudioEvent] = []
    spm = float(args.seconds_per_move)

    for i, s in enumerate(states, start=1):
        board.push(s.last_move)
        _print_log(s)

        render_frame(
            board=board, state=s, meta=meta, fonts=fonts,
            out_path=frames_dir / f"frame_{i:03d}.png",
        )

        t = i * spm
        if s.is_checkmate:
            events.append(AudioEvent(t=t, kind="mate"))
        elif s.attacked_enemy_squares:
            events.append(AudioEvent(t=t, kind="shot"))
        elif not args.no_click:
            events.append(AudioEvent(t=t, kind="click"))

    total_duration = (len(states) + 1) * spm

    print("Preparing audio assets ...")
    gunshot = ensure_gunshot(assets_dir)
    click = ensure_click(assets_dir) if not args.no_click else gunshot

    audio_path = project_root / "out" / "_audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Building audio timeline ({len(events)} events, "
          f"{total_duration:.1f}s) ...")
    build_audio_track(
        events=events,
        total_duration=total_duration,
        gunshot=gunshot,
        click=click,
        out_path=audio_path,
    )

    print(f"Muxing video -> {out_path}")
    mux_video(
        frames_dir=frames_dir,
        audio_path=audio_path,
        out_path=out_path,
    )

    if not args.keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
        try:
            audio_path.unlink()
        except FileNotFoundError:
            pass

    n_attack = sum(1 for e in events if e.kind in ("shot", "mate"))
    print(f"Done. Wrote {out_path} ({total_duration:.1f}s, "
          f"{n_attack} attacking plies).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
