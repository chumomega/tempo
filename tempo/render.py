"""Render 1080x1920 frames from PlyState data."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import cairosvg
import chess
import chess.svg
from PIL import Image, ImageDraw, ImageFont

from .engine import GameMeta, PlyState

# --- canvas geometry ---
W, H = 1080, 1920
HEADER_H = 250
FOOTER_H = 250
BOARD_PX = 1000
BOARD_X = (W - BOARD_PX) // 2                              # 40
BOARD_Y = HEADER_H + (H - HEADER_H - FOOTER_H - BOARD_PX) // 2  # 460
SQ = BOARD_PX // 8                                          # 125

# --- colors ---
LIGHT = "#F0D9B5"
DARK = "#B58863"
BG = (24, 24, 28)
HEADER_BG = (32, 32, 40)
FOOTER_BG = (32, 32, 40)
TEXT_FG = (240, 240, 240)
TEXT_DIM = (170, 170, 180)
HIGHLIGHT_RGBA = (255, 215, 0, 110)   # yellow tint, ~40% alpha
RING_RGBA = (230, 35, 35, 240)        # red ring around attacked enemies
MATE_RED = (220, 40, 40)


# Fonts: pick whatever exists on macOS, fall back to default bitmap font.
_FONT_CANDIDATES_BOLD = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]
_FONT_CANDIDATES_REG = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REG
    for p in paths:
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


@dataclass
class Fonts:
    name: ImageFont.ImageFont
    elo: ImageFont.ImageFont
    move_num: ImageFont.ImageFont
    footer: ImageFont.ImageFont
    mate: ImageFont.ImageFont

    @classmethod
    def build(cls) -> "Fonts":
        return cls(
            name=_load_font(54, bold=True),
            elo=_load_font(36, bold=False),
            move_num=_load_font(96, bold=True),
            footer=_load_font(110, bold=True),
            mate=_load_font(140, bold=True),
        )


def _square_xy(sq: chess.Square) -> tuple[int, int]:
    """Top-left pixel of a square within the board image (white-perspective)."""
    file = chess.square_file(sq)
    rank = chess.square_rank(sq)
    return file * SQ, (7 - rank) * SQ


def _render_board_png(board: chess.Board, lastmove: chess.Move | None) -> Image.Image:
    """Rasterize the board (no coordinates, no built-in highlight)."""
    svg = chess.svg.board(
        board=board,
        size=BOARD_PX,
        coordinates=False,
        lastmove=None,           # we draw our own highlight to control color
        check=None,
        colors={
            "square light": LIGHT,
            "square dark": DARK,
            "margin": "#00000000",
        },
    )
    png_bytes = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=BOARD_PX,
        output_height=BOARD_PX,
    )
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return img


def _draw_highlight(board_img: Image.Image, sq: chess.Square) -> None:
    overlay = Image.new("RGBA", board_img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    x, y = _square_xy(sq)
    d.rectangle([x, y, x + SQ, y + SQ], fill=HIGHLIGHT_RGBA)
    board_img.alpha_composite(overlay)


def _draw_attack_ring(board_img: Image.Image, sq: chess.Square) -> None:
    overlay = Image.new("RGBA", board_img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    x, y = _square_xy(sq)
    pad = 8
    d.ellipse(
        [x + pad, y + pad, x + SQ - pad, y + SQ - pad],
        outline=RING_RGBA,
        width=8,
    )
    board_img.alpha_composite(overlay)


def _draw_text_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    cx: int,
    cy: int,
    font: ImageFont.ImageFont,
    fill,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text((cx - w / 2 - bbox[0], cy - h / 2 - bbox[1]), text, font=font, fill=fill)


def _draw_text_left(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    cy: int,
    font: ImageFont.ImageFont,
    fill,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    h = bbox[3] - bbox[1]
    draw.text((x - bbox[0], cy - h / 2 - bbox[1]), text, font=font, fill=fill)


def _draw_text_right(
    draw: ImageDraw.ImageDraw,
    text: str,
    x_right: int,
    cy: int,
    font: ImageFont.ImageFont,
    fill,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text((x_right - w - bbox[0], cy - h / 2 - bbox[1]), text, font=font, fill=fill)


def _move_number_label(ply_index: int) -> str:
    """e.g. '13.' after white's 13th move, '13...' after black's."""
    move_num = (ply_index + 1) // 2
    is_white_move = (ply_index % 2) == 1
    return f"{move_num}." if is_white_move else f"{move_num}..."


def _footer_san(ply_index: int, san: str, is_checkmate: bool) -> str:
    if is_checkmate:
        return "CHECKMATE"
    return f"{_move_number_label(ply_index)} {san}"


def render_frame(
    *,
    board: chess.Board,
    state: PlyState | None,
    meta: GameMeta,
    fonts: Fonts,
    out_path: Path,
) -> None:
    """Render a single frame to PNG. state=None means starting position."""
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(canvas)

    # --- header ---
    draw.rectangle([0, 0, W, HEADER_H], fill=HEADER_BG)
    pad = 32
    name_y = HEADER_H // 2 - 22
    elo_y = HEADER_H // 2 + 36

    white_label = meta.white or "White"
    black_label = meta.black or "Black"
    _draw_text_left(draw, white_label, pad, name_y, fonts.name, TEXT_FG)
    if meta.white_elo:
        _draw_text_left(draw, meta.white_elo, pad, elo_y, fonts.elo, TEXT_DIM)
    _draw_text_right(draw, black_label, W - pad, name_y, fonts.name, TEXT_FG)
    if meta.black_elo:
        _draw_text_right(draw, meta.black_elo, W - pad, elo_y, fonts.elo, TEXT_DIM)

    move_label = _move_number_label(state.ply_index) if state else ""
    if move_label:
        _draw_text_centered(draw, move_label, W // 2, HEADER_H // 2,
                            fonts.move_num, TEXT_FG)

    # --- board ---
    board_img = _render_board_png(board, state.last_move if state else None)
    if state is not None:
        _draw_highlight(board_img, state.from_square)
        _draw_highlight(board_img, state.to_square)
        for sq in state.attacked_enemy_squares:
            _draw_attack_ring(board_img, sq)
    canvas.alpha_composite(board_img, dest=(BOARD_X, BOARD_Y))

    # --- footer ---
    draw.rectangle([0, H - FOOTER_H, W, H], fill=FOOTER_BG)
    if state is None:
        # starting position frame: just show the result tag
        _draw_text_centered(draw, "Start", W // 2, H - FOOTER_H // 2,
                            fonts.footer, TEXT_FG)
    elif state.is_checkmate:
        _draw_text_centered(draw, "CHECKMATE", W // 2, H - FOOTER_H // 2,
                            fonts.mate, MATE_RED)
    else:
        _draw_text_centered(
            draw,
            _footer_san(state.ply_index, state.san, False),
            W // 2,
            H - FOOTER_H // 2,
            fonts.footer,
            TEXT_FG,
        )

    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
