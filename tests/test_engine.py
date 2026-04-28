"""Sanity checks for attack detection on the sample game.

Note on naming in the spec:
- "Re1+ attacks the e6 bishop" — at the *moment* of 13. Re1+ the black bishop
  is still on d7 (Be6 is Black's reply, blocking the check). What Re1+ actually
  newly attacks is the black king on e8.
- "Qxe8+ attacks the rook on e8" — Qxe8 captures that rook, so by the time
  the position is evaluated the rook is gone. What the queen attacks from e8
  is the black king on h8.
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
    assert not s.is_capture
    assert chess.E8 in s.attacked_enemy_squares
    # Rule B: attacker (rook=5) attacking the king only — king is excluded
    # from gunshot targets, so this move should NOT fire the gunshot.
    assert s.gunshot_targets == []


def test_qxe8_check_is_capture_and_no_gunshot():
    s = _states()[50]  # 26. Qxe8+
    assert s.san == "Qxe8+"
    assert s.is_check
    assert s.is_capture
    assert chess.H8 in s.attacked_enemy_squares
    # Queen (9) attacking only smaller pieces + king -> no rule-B gunshot.
    assert s.gunshot_targets == []


def test_final_move_is_mate_and_no_gunshot():
    s = _states()[-1]
    assert s.is_checkmate is True
    assert s.san == "Rxe8#"
    assert s.is_capture
    # Rook (5) attacks only the king from e8 -> no gunshot, mate layer takes
    # over the punctuation.
    assert s.gunshot_targets == []
    assert chess.H8 in s.attacked_enemy_squares


def test_pawn_fork_triggers_gunshot():
    # 5... d5 attacks both the bishop on c4 and the pawn on e4 — pawn (1)
    # vs bishop (3) and pawn (1) both satisfy attacker <= defender.
    states = _states()
    s = states[9]
    assert s.san == "d5"
    assert s.move_number == 5
    targets = set(s.gunshot_targets)
    assert chess.C4 in targets
    assert chess.E4 in targets


def test_castling_flag():
    states = _states()
    s = states[8]  # 5. O-O
    assert s.san == "O-O"
    assert s.is_castling
    assert not s.is_capture


def test_quiet_developing_move_no_gunshot():
    # 1. e4: pawn attacks d5 and f5, both empty -> no enemies, no gunshot.
    states = _states()
    s = states[0]
    assert s.san == "e4"
    assert s.gunshot_targets == []
    assert s.attacked_enemy_squares == []
