"""PGN parsing + per-ply state with attack detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn


PIECE_VALUE: dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,  # never used as a gunshot target; kings trigger fahh.
}


@dataclass
class PlyState:
    """Snapshot of one half-move plus the position after it."""

    ply_index: int            # 1-based; 0 reserved for the starting position
    move_number: int          # full-move number, e.g. 13 for "13. Re1+"
    mover_color: chess.Color  # color that just moved
    san: str                  # SAN of the move just played, e.g. "Re1+"
    from_square: chess.Square
    to_square: chess.Square
    attacked_enemy_squares: list[chess.Square]  # all enemies attacked, incl. king
    gunshot_targets: list[chess.Square]         # subset that fires the gunshot
    is_check: bool
    is_checkmate: bool
    is_capture: bool
    is_castling: bool
    fen_after: str
    last_move: chess.Move


@dataclass
class GameMeta:
    white: str
    black: str
    white_elo: str
    black_elo: str
    result: str


def _hdr(headers, key: str, default: str = "?") -> str:
    val = headers.get(key, default)
    return val if val else default


def load_game(pgn_path: Path) -> tuple[GameMeta, chess.pgn.Game]:
    with pgn_path.open("r", encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    if game is None:
        raise ValueError(f"No game found in {pgn_path}")
    h = game.headers
    meta = GameMeta(
        white=_hdr(h, "White"),
        black=_hdr(h, "Black"),
        white_elo=_hdr(h, "WhiteElo", ""),
        black_elo=_hdr(h, "BlackElo", ""),
        result=_hdr(h, "Result", "*"),
    )
    return meta, game


def iter_plies(game: chess.pgn.Game) -> list[PlyState]:
    """Walk the mainline and return one PlyState per half-move."""
    board = game.board()
    states: list[PlyState] = []

    for i, move in enumerate(game.mainline_moves(), start=1):
        san = board.san(move)
        mover_color = board.turn
        from_sq = move.from_square
        to_sq = move.to_square
        is_capture = board.is_capture(move)
        is_castling = board.is_castling(move)

        board.push(move)

        # Attacks from the destination square. python-chess's attacks() returns
        # the squares this piece could capture on; intersect with enemy pieces
        # currently on the board to get real targets.
        moved_piece = board.piece_at(to_sq)
        attacked: list[chess.Square] = []
        gunshot_targets: list[chess.Square] = []
        if moved_piece is not None:
            attack_mask = board.attacks(to_sq)
            enemy_color = not moved_piece.color
            enemy_mask = board.occupied_co[enemy_color]
            attacker_value = PIECE_VALUE[moved_piece.piece_type]
            for sq in chess.scan_forward(int(attack_mask) & int(enemy_mask)):
                attacked.append(sq)
                target = board.piece_at(sq)
                if target is None or target.piece_type == chess.KING:
                    continue
                if attacker_value <= PIECE_VALUE[target.piece_type]:
                    gunshot_targets.append(sq)

        states.append(
            PlyState(
                ply_index=i,
                move_number=(i + 1) // 2,
                mover_color=mover_color,
                san=san,
                from_square=from_sq,
                to_square=to_sq,
                attacked_enemy_squares=attacked,
                gunshot_targets=gunshot_targets,
                is_check=board.is_check(),
                is_checkmate=board.is_checkmate(),
                is_capture=is_capture,
                is_castling=is_castling,
                fen_after=board.fen(),
                last_move=move,
            )
        )

    return states
