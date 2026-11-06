"""Builds the S&P 500 stock list for the site's S&P 500 option.

Reads Wikipedia's "List of S&P 500 companies" (symbol, company, GICS sector) and writes
  engine/tickers500.txt   one Yahoo symbol per line (BRK.B -> BRK-B)
  engine/names500.json    {ticker: [name, sector]}, sectors named like engine/names.json
  docs/data/universe.json both lists and the names, for the site's ticker boxes
The S&P 100 names in engine/names.json are kept as they are, so both lists read the same.
If Wikipedia can't be read or looks wrong (fewer than 480 rows), the files are left as they are;
with no file yet, the current members in research/quant/data/sp500_membership.csv are used.
Runs on the first day of every month (.github/workflows/sp500-list.yml) and when this file changes.

    python engine/sp500_list.py
    python engine/sp500_list.py --seed     # offline: from the membership file only
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parent
URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SECTOR = {"Information Technology": "Technology", "Communication Services": "Communication"}


class _Table(HTMLParser):
    """Rows of the first table with id="constituents"."""
    def __init__(self):
        super().__init__()
        self.on, self.depth, self.rows, self.row, self.cell, self.in_cell = False, 0, [], None, [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self.on:
                self.depth += 1
            elif a.get("id") == "constituents":
                self.on, self.depth = True, 1
        elif self.on and self.depth == 1 and tag == "tr":
            self.row = []
        elif self.on and self.depth == 1 and tag in ("td", "th"):
            self.in_cell, self.cell = True, []

    def handle_endtag(self, tag):
        if not self.on:
            return
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self.on = False
        elif self.depth != 1:
            return
        elif tag in ("td", "th") and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.in_cell = False
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


def short_name(name: str) -> str:
    """ "The Hershey Company" -> "Hershey Company", "Apple Inc." -> "Apple": closer to how headlines write it."""
    n = name.strip()
    if n.startswith("The "):
        n = n[4:]
    for suf in (", Inc.", " Inc.", " Incorporated", " Corporation", " Corp.", " plc", " PLC", " Ltd.", " N.V.", " Co.", ","):
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
    return n


def yahoo(sym: str) -> str:
    return sym.strip().replace(".", "-")


def from_wikipedia() -> dict[str, list[str]]:
    req = urllib.request.Request(URL, headers={"User-Agent": "signal-desk/1.0 (stock list builder)"})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    p = _Table()
    p.feed(html)
    head, *body = p.rows
    i_sym, i_name, i_sec = head.index("Symbol"), head.index("Security"), head.index("GICS Sector")
    out = {}
    for r in body:
        if len(r) > max(i_sym, i_name, i_sec):
            out[yahoo(r[i_sym])] = [short_name(r[i_name]), SECTOR.get(r[i_sec], r[i_sec])]
    return out


def from_membership() -> dict[str, list[str]]:
    f = ROOT / "research" / "quant" / "data" / "sp500_membership.csv"
    with f.open() as fh:
        return {yahoo(r["ticker"]): [r["ticker"], "Other"] for r in csv.DictReader(fh) if not r["end_date"]}


def worker_list(tickers: list[str]) -> None:
    """Keep the data Worker's S&P 500 list (worker/trendlines-worker.js, between SP500-START/END) in step.
    Cloudflare only gets it when the Worker is pasted again."""
    f = ROOT / "worker" / "trendlines-worker.js"
    if not f.exists():
        return
    s = f.read_text()
    a, b = s.find("// SP500-START\n"), s.find("// SP500-END")
    if a < 0 or b < 0:
        return
    new = s[:a] + "// SP500-START\n" + f'const SP500 = new Set("{" ".join(tickers)}".split(" "));\n' + s[b:]
    if new != s:
        f.write_text(new)
        print("worker/trendlines-worker.js: S&P 500 list updated (paste the Worker again)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    a = ap.parse_args()
    small = json.loads((ENGINE / "names.json").read_text())
    names_f, tick_f = ENGINE / "names500.json", ENGINE / "tickers500.txt"
    old = json.loads(names_f.read_text()) if names_f.exists() else {}
    if a.seed:
        got = from_membership()
    else:
        try:
            got = from_wikipedia()
        except Exception as e:
            print(f"Wikipedia not readable: {e}")
            got = {}
        if len(got) < 480:
            print(f"Only {len(got)} rows from Wikipedia: list left as it is.")
            if old:
                return
            got = from_membership()
    for t, v in small.items():                        # every S&P 100 stock is an S&P 500 stock
        got.setdefault(t, v)
    names = {}
    for t, (name, sector) in sorted(got.items()):
        if t in small:
            names[t] = small[t]                       # the S&P 100 keeps its own names
        elif sector == "Other" and t in old and old[t][1] != "Other":
            names[t] = old[t]
        else:
            names[t] = [name, sector]
    names_f.write_text(json.dumps(names, indent=1) + "\n")
    tick_f.write_text("# S&P 500 constituents (Yahoo Finance symbols), written by engine/sp500_list.py.\n"
                      "# The S&P 100 list is engine/tickers.txt; every S&P 100 stock is also in here.\n"
                      + "\n".join(names) + "\n")
    worker_list(list(names))
    # for the site's ticker boxes (chart, trendlines, options, volume profile): both lists and the names
    small_list = [x.strip() for x in (ENGINE / "tickers.txt").read_text().splitlines() if x.strip() and not x.startswith("#")]
    (ROOT / "docs" / "data" / "universe.json").write_text(json.dumps(
        {"sp100": small_list, "sp500": list(names), "names": {t: v[0] for t, v in names.items()}}, separators=(",", ":")))
    added, gone = sorted(set(names) - set(old)), sorted(set(old) - set(names))
    print(f"{len(names)} stocks -> {tick_f.name}, {names_f.name}" + (f"; added {added[:20]}" if old and added else "")
          + (f"; removed {gone[:20]}" if gone else ""))
    miss = [t for t in small if t not in names and t != "GOOG"]
    if miss:
        print(f"  note: S&P 100 stocks not in the list: {miss}", file=sys.stderr)


if __name__ == "__main__":
    main()
