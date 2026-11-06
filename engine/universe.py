"""Which stock list a run works on: the S&P 100 (default) or the S&P 500.

Set the environment variable UNIVERSE=sp500 (the workflows do) and every stock script reads
engine/tickers500.txt / engine/names500.json and writes under docs/data/sp500/ instead of docs/data/,
with the same file names inside. Phone alert topics get an "sp500-" prefix so the bigger list
never floods the S&P 100 channels. The site's S&P 100 / S&P 500 switch reads the matching folder.

    from universe import U
    U.tickers_file, U.names_file, U.data / "alerts.json", U.label, U.topic("choch")
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"


class _Universe:
    def __init__(self, key: str):
        self.key = "sp500" if key.strip().lower() in ("sp500", "s&p500", "500") else "sp100"
        big = self.key == "sp500"
        self.is500 = big
        self.label = "S&P 500" if big else "S&P 100"
        self.tickers_file = ENGINE / ("tickers500.txt" if big else "tickers.txt")
        self.names_file = ENGINE / ("names500.json" if big else "names.json")
        self.data = ROOT / "docs" / "data" / ("sp500" if big else "")
        self.prefix = "sp500-" if big else ""

    def path(self, *parts: str) -> Path:
        """docs/data/<parts> for the S&P 100, docs/data/sp500/<parts> for the S&P 500."""
        return self.data.joinpath(*parts)

    def topic(self, name: str = "") -> str:
        """ntfy topic suffix: "" / "choch" for the S&P 100, "sp500" / "sp500-choch" for the S&P 500."""
        return (self.prefix + name) if name else self.prefix.rstrip("-")

    def tickers(self, drop: tuple[str, ...] = ()) -> list[str]:
        raw = self.tickers_file.read_text().splitlines()
        return [x.strip() for x in raw if x.strip() and not x.startswith("#") and x.strip() not in drop]


U = _Universe(os.environ.get("UNIVERSE", "sp100"))
