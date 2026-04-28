# CLAUDE.md

Guide for working on this repo with Claude Code (or any AI pair).

## What this project is

`tempo` turns a chess PGN into a 1080x1920 MP4 short. The signature gimmick:
a gunshot SFX fires every time a piece moves to a square from which it
attacks an enemy piece. Soft click on quiet moves, gunshot + reverb tail on
mate. Designed as a starting point for CapCut — render at a steady pace,
no narration baked in.

## Architecture

Four modules under `tempo/`, one CLI entry. The pipeline is linear:

```
PGN ──► engine.iter_plies ──► [PlyState, ...]
                                    │
                         ┌──────────┴──────────┐
                         ▼                     ▼
                   render.render_frame   audio.build_audio_track
                         │                     │
                         ▼                     ▼
                  frames/frame_NNN.png    out/_audio.wav
                         │                     │
                         └──────────┬──────────┘
                                    ▼
                            audio.mux_video
                                    │
                                    ▼
                          out/chess_short.mp4
```

- **`engine.py`** — wraps `python-chess`. `iter_plies(game)` walks the
  mainline and emits one `PlyState` per half-move with: SAN, mover color,
  from/to squares, the set of enemy squares attacked from `to_square`, and
  `is_check` / `is_checkmate`. The attack set is
  `board.attacks(to_sq) & board.occupied_co[enemy_color]`.
- **`render.py`** — composites a 1080x1920 PNG per ply. Header (250px) shows
  player names + Elo + move number. Board is rendered via `chess.svg.board()`
  → `cairosvg` → PIL. Yellow tint on last from/to squares, red rings around
  attacked enemies, footer SAN big and bold (red "CHECKMATE" on mate).
- **`audio.py`** — pulls the gunshot SFX from YouTube via `yt-dlp`, trims it
  to a punchy 0.6s with `ffmpeg`'s `silenceremove` + `loudnorm`. Builds the
  timeline by feeding ffmpeg N copies of the gunshot/click as inputs and
  using `adelay` per event, then `amix`. Mate event also gets `aecho`.
- **`pgn_to_short.py`** — CLI; wires engine → render → audio. Renders frames
  to `frames/`, then ffmpeg muxes the PNG sequence (1 fps in, 30 fps out)
  with the audio track to `out/chess_short.mp4`.

## Layout

```
tempo/
  __init__.py
  engine.py          # PGN parsing + attack detection
  pgn_to_short.py    # CLI entry
  render.py          # Pillow frame composition
  audio.py           # yt-dlp + ffmpeg pipeline
tests/
  test_engine.py     # attack-detection sanity checks
assets/              # downloaded SFX (gitignored)
out/                 # final mp4 + intermediate audio (gitignored)
frames/              # per-ply PNGs (gitignored)
game.pgn             # input
```

## Run

```bash
source .venv/bin/activate
python -m tempo.pgn_to_short game.pgn         # default settings
python -m tempo.pgn_to_short --keep-frames    # keep intermediate PNGs
python -m tempo.pgn_to_short --seconds-per-move 1.5
python -m tempo.pgn_to_short --start-from 13  # only render move 13 onward
pytest tests/
```

## Things to know before editing

- **Frame count = plies + 1.** There's one frame per ply *plus* a frame for
  the starting position (`frame_000.png`). Total duration is
  `(n_plies + 1) * seconds_per_move`. Audio events for ply N fire at
  `t = N * seconds_per_move`.
- **Frame timing and audio offsets must agree.** Video stitches at `fps_in=1`
  and the mix uses 1.0s offsets by default. The `--seconds-per-move` flag
  changes both — don't change one without the other.
- **`board.attacks(sq)` returns potential targets, not actual ones.** Always
  intersect with `board.occupied_co[enemy_color]` to get real enemy squares.
  Pawns attack diagonally only; `chess.Board.attacks` already encodes that.
- **The board SVG is rendered with `coordinates=False`.** That makes the
  8x8 grid fill exactly 1000x1000, so each square is 125px. If you turn
  coordinates back on you'll need to redo the `_square_xy()` math.
- **Gunshot is cached at `assets/gunshot.wav`.** Delete it to force a
  re-download. The download runs once per fresh checkout.
- **Many ffmpeg inputs.** The mix opens the gunshot/click WAV once per event
  (~50 inputs for a typical game). FFmpeg handles this fine; don't try to
  "optimize" with stream-copies — `adelay` needs distinct stream IDs.

## Testing changes to attack detection

Use the bundled `game.pgn` (chumomega vs AnhDT1907). Quick checks:

- `13. Re1+` should newly attack `e8` (the king). `Be6` is Black's *reply*,
  so the bishop is still on d7 at the moment Re1+ is played.
- `26. Qxe8+` should attack `h8` (the king). The rook on e8 has been
  captured, so it's no longer a target.
- `27. Rxe8#` is `is_checkmate=True` and attacks `h8`.

Run `pytest tests/` to verify all three.

## Common change requests

- **Different SFX** — override `GUNSHOT_URL` in `audio.py`, or drop a custom
  WAV at `assets/gunshot.wav` and the download is skipped.
- **Different highlight colors** — edit `HIGHLIGHT_RGBA` / `RING_RGBA` in
  `render.py`. The board square colors are the `LIGHT` / `DARK` constants.
- **Different output resolution** — edit `W` / `H` / `BOARD_PX` in
  `render.py`. Keep `BOARD_PX % 8 == 0` so square math stays integer.
- **Tweak gunshot loudness** — `volume=` argument in
  `audio.build_audio_track`, per event kind.
