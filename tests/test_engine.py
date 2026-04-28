"""Sanity checks for attack detection on the sample game.

Note on naming in the spec:
- "Re1+ attacks the e6 bishop" — at the *moment* of 13. Re1+ the black bishop
  is still on d7 (Be6 is Black's reply, blocking the check). What Re1+ actually
  newly attacks is the black king on e8.
- "Qxe8+ attacks the rook on e8" — Qxe8 captures that rook, so by the time
  the position is evaluated the rook is gone. What the queen attacks from e8
  is the black king on h8.
Both moves correctly fire the gunshot trigger because both attack the king.
"""

from __future__ import annotations

from pathlib import Path

import chess

from tempo.engine import iter_plies, load_game


SAMPLE_PGN = Path(__file__).resolve().parent.parent / "game.pgn"


def _states():
    _, game = load_game(SAMPLE_PGN)
    return iter_plies(game)


def test_re1_check_attacks_king():
    s = _states()[24]  # 13. Re1+ -> ply 25
    assert s.san == "Re1+"
    assert s.move_number == 13
    assert s.is_check
    assert chess.E8 in s.attacked_enemy_squares
    # And it should fire the gunshot trigger.
    assert len(s.attacked_enemy_squares) >= 1


def test_qxe8_check_attacks_king():
    s = _states()[50]  # 26. Qxe8+ -> ply 51
    assert s.san == "Qxe8+"
    assert s.move_number == 26
    assert s.is_check
    assert chess.H8 in s.attacked_enemy_squares


def test_final_move_is_mate_and_attacks_king():
    s = _states()[-1]
    assert s.is_checkmate is True
    assert s.san == "Rxe8#"
    assert chess.H8 in s.attacked_enemy_squares
