"""Day-by-day series for the Strategy Board (docs/board.html).

A strategy that is backtested by its own script (not by the Strategy Lab) saves one number per
trading day it was in the market, the whole account's change that day, in the backtest JSON under
"board". engine/board_extra.py gathers those into docs/data/board_extra.json for the board.

    unit: 0.1 basis point (12345 = +1.2345%); days not listed = no position (flat)
"""
from __future__ import annotations


def pack(daily: list) -> dict:
    """[[date, change as a fraction], ...] -> {"unit", "dates", "days"} in 0.1bp ints."""
    daily = sorted((str(d)[:10], r) for d, r in daily if r is not None)
    return {"unit": "0.1bp", "dates": [d for d, _ in daily], "days": [round(r * 1e5) for _, r in daily]}


def strip(obj, key: str = "_daily"):
    """Drop the per-day lists that stats() carries along, so only the "board" block is saved."""
    if isinstance(obj, dict):
        obj.pop(key, None)
        for v in obj.values():
            strip(v, key)
    elif isinstance(obj, list):
        for v in obj:
            strip(v, key)
    return obj


def from_points(points: list, start: float = 1.0) -> list:
    """Equity points [(date, equity), ...] (several a day allowed, in time order) -> [[date, change], ...]
    using each day's last value; start is the equity before the first point."""
    last: dict[str, float] = {}
    for d, e in points:
        last[str(d)[:10]] = e
    out, prev = [], start
    for d in sorted(last):
        e = last[d]
        if prev and prev > 0:
            out.append([d, e / prev - 1])
        prev = e
    return out
