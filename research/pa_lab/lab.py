"""Loading markets for discovery (warm-up + discovery, never the sealed window) and the decision log."""
from __future__ import annotations

from datetime import datetime, timezone

from . import config as C
from .charts import Market, build_market
from .data import load_clean


def discovery_market(sym: str) -> Market:
    df = load_clean(sym)
    df = df[(df["session"] >= C.WARMUP[0]) & (df["session"] <= C.DISCOVERY[1])]
    return build_market(sym, df)


def log_decision(code: str, text: str) -> None:
    p = C.ROOT / "decisions.md"
    if code in p.read_text():
        return
    with open(p, "a") as f:
        f.write(f"\n- **{code}** ({datetime.now(timezone.utc):%Y-%m-%d}): {text}\n")
