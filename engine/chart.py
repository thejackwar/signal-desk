"""Turns a DataFrame + StrategyResult into the compact JSON the web app draws."""
import numpy as np


def _r(v):
    """Round a price: 2 decimals normally, more for cheap coins (5 significant digits)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    v = float(v)
    a = abs(v)
    return round(v, 2 if a >= 1 or a == 0 else min(10, 4 - int(np.floor(np.log10(a)))))


def payload(df, res, start: int, end: int | None = None, only_marker: int | None = None) -> dict:
    """Chart window [start, end). With only_marker, show just that alert and its drawing."""
    end = len(df) if end is None else end
    sl = slice(start, end)
    d = df.iloc[sl]
    out = {
        "dates": [x.strftime("%Y-%m-%d") for x in d.index],
        "o": [_r(v) for v in d["open"]], "h": [_r(v) for v in d["high"]],
        "l": [_r(v) for v in d["low"]], "c": [_r(v) for v in d["close"]],
        "overlays": [{**o, "values": [_r(v) for v in o["values"][sl]]} for o in res.overlays],
        "markers": [],
        "shapes": [],
    }
    for i in np.flatnonzero(res.signals.values):
        i = int(i)
        if start <= i < end and (only_marker is None or i == only_marker):
            out["markers"].append({"i": i - start, "side": int(res.signals.iloc[i]),
                                   "note": res.notes.get(i, "")})
    for s in res.shapes:
        xs = [p[0] for p in s["points"]]
        inside = min(xs) >= start and max(xs) < end
        mine = only_marker is None or s.get("at") == only_marker
        if inside and mine:
            out["shapes"].append({**{k: v for k, v in s.items() if k != "at"}, "points": [[int(p[0]) - start, _r(p[1])] for p in s["points"]]})
    if res.panel:
        p = res.panel
        out["panel"] = {**p, "lines": [{**ln, "values": [_r(v) for v in ln["values"][sl]]} for ln in p["lines"]]}
        if "hist" in p:
            out["panel"]["hist"] = [_r(v) for v in p["hist"][sl]]
    return out
