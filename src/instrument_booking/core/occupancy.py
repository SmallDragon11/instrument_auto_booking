"""判斷預約表儲存格是否為空（規則見 docs/表格結構.md §5）。"""
from __future__ import annotations

from openpyxl.cell.cell import MergedCell

from instrument_booking.core.models import CellFont, CellState

WHITE = "FFFFFF"
MERGED = "merged"
UNKNOWN = "unknown"


def normalize_rgb(rgb: str) -> str:
    """'FFA4C2F4' 或 'a4c2f4' → 'A4C2F4'。"""
    return rgb.upper()[-6:]


def cell_state(cell) -> CellState:
    """openpyxl 儲存格 → CellState。不認得的顏色一律保留為非 None（視為已佔用）。"""
    if isinstance(cell, MergedCell):
        return CellState(value=None, color=MERGED)
    fill = cell.fill
    color = None
    if fill is not None and fill.fill_type is not None:
        fg = getattr(fill, "fgColor", None)
        if fg is None:
            color = UNKNOWN  # 漸層填色等沒有 fgColor 的填色
        elif fg.type == "rgb":
            rgb = normalize_rgb(fg.rgb)
            color = None if rgb == WHITE else rgb
        elif fg.type == "theme":
            color = None if fg.theme == 0 and not fg.tint else f"theme:{fg.theme}"
        else:
            color = f"{fg.type}:{fg.indexed}"
    return CellState(value=cell.value, color=color)


def is_empty(state: CellState) -> bool:
    return state.value in (None, "") and state.color is None


def cell_font(cell) -> CellFont:
    """快照中儲存格原本的字型（貼上時逐格寫回）。"""
    if isinstance(cell, MergedCell):
        return CellFont(size=None, bold=False, h_align=None)
    font, align = cell.font, cell.alignment
    return CellFont(
        size=float(font.sz) if font is not None and font.sz is not None else None,
        bold=bool(font is not None and font.b),
        h_align=align.horizontal if align is not None else None,
    )
