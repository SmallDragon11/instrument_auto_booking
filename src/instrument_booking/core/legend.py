"""氣體圖例與烘箱顏色（規則見 docs/表格結構.md §3、§4）。"""
from __future__ import annotations

import re

from instrument_booking.core.models import BookingRequest
from instrument_booking.core.occupancy import cell_state

OVEN_DEFAULT = {0: "FFE599", 1: "A4C2F4"}
_RGB6 = re.compile(r"^[0-9A-F]{6}$")


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name).casefold()


def _rgb(cell) -> str | None:
    """只接受一般 RGB 底色（非白、非主題色）。"""
    color = cell_state(cell).color
    return color if color is not None and _RGB6.match(color) else None


def read_tube_legend(ws) -> dict[str, str]:
    legend: dict[str, str] = {}
    col = 2
    while True:
        cell = ws.cell(2, col)
        color = _rgb(cell)
        if cell.value in (None, "") or color is None:
            break
        legend[str(cell.value).strip()] = color
        col += 1
    return legend


def lookup_gas(legend: dict[str, str], gas: str) -> str | None:
    wanted = _norm(gas)
    return next((color for name, color in legend.items() if _norm(name) == wanted), None)


def oven_color(ws, sub_index: int) -> str:
    cell = ws.cell(1, 2 + sub_index)
    color = _rgb(cell)
    if str(cell.value or "").strip() == "AB"[sub_index] and color is not None:
        return color
    return OVEN_DEFAULT[sub_index]


def resolve_color(ws, req: BookingRequest) -> tuple[str, str | None]:
    if req.instrument.kind == "oven":
        return oven_color(ws, req.instrument.sub_index), None
    color = lookup_gas(read_tube_legend(ws), req.gas)
    if color is None:
        return req.color, f"圖例找不到 {req.gas}，改用選擇時的顏色 {req.color}"
    return color, None
