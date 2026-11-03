"""
Options paper trades: every options strategy of the Options page trades one SPY contract from the
go-live day on, with the same monthly rules as the backtest (engine/options_backtest.py, hold to
expiry). Ledger: docs/data/options/live.json.

  - Prices: real SPY option quotes from Alpaca (secrets ALPACA_KEY_ID / ALPACA_SECRET_KEY), buying at
    the ask and selling at the bid, plus the commission. If the quotes can't be fetched, the model
    (engine/options_model.py, with VIX) is used instead and the trade is marked "model".
  - Strikes: picked by delta on the real chain, by the same rule as the backtest.
  - At expiry the options are settled at SPY's close (a calendar's longer option is sold at the bid).
  - Closed trades are never changed or removed; open positions are re-marked on every run.
  - Paper only: no orders are sent anywhere.

  python engine/options_live.py          # GitHub Actions, after the daily scan
  python engine/options_live.py --demo   # simulated prices, writes to a scratch file
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import options_model as M  # noqa: E402

ENGINE = Path(__file__).parent
OUT = ENGINE.parent / "docs" / "data" / "options" / "live.json"
UNDERLYING = "SPY"
SNAP = "https://data.alpaca.markets/v1beta1/options/snapshots/"


# ------------------------------------------------------------------ prices
def spy_and_vix(demo: bool):
    """SPY closes (date -> close) for the last ~2 months and the latest VIX level."""
    if demo:
        import numpy as np
        rng = np.random.default_rng(int(os.environ.get("DEMO_SEED", "1")))
        import pandas as pd
        idx = pd.bdate_range(end=os.environ.get("DEMO_TODAY") or date.today().isoformat(), periods=60)
        c = 680 * np.exp(np.cumsum(rng.normal(0.0003, 0.009, len(idx))))
        return {d.date(): float(v) for d, v in zip(idx, c)}, 17.0
    import yfinance as yf
    raw = yf.download(["SPY", "^VIX"], period="3mo", interval="1d", group_by="ticker", auto_adjust=False,
                      progress=False, threads=True)
    spy = raw["SPY"]["Close"].dropna()
    vix = float(raw["^VIX"]["Close"].dropna().iloc[-1])
    return {d.date(): float(v) for d, v in spy.items()}, vix


def alpaca_chain(expiries: list[date], S: float) -> dict | None:
    """{(expiry iso, "C"/"P", strike): {bid, ask, iv, delta}} from Alpaca's option snapshots, or None."""
    key, sec = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        return None
    out = {}
    try:
        for e in sorted(set(expiries)):
            params = {"feed": "indicative", "expiration_date": e.isoformat(), "limit": "1000",
                      "strike_price_gte": f"{S * 0.7:.0f}", "strike_price_lte": f"{S * 1.3:.0f}"}
            token = None
            for _ in range(10):
                if token:
                    params["page_token"] = token
                req = urllib.request.Request(SNAP + UNDERLYING + "?" + urllib.parse.urlencode(params),
                                             headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
                js = json.loads(urllib.request.urlopen(req, timeout=30).read())
                for occ, snap in (js.get("snapshots") or {}).items():
                    p = parse_occ(occ)
                    if not p:
                        continue
                    q = snap.get("latestQuote") or {}
                    g = snap.get("greeks") or {}
                    out[(p[0], p[1], p[2])] = {"bid": float(q.get("bp") or 0), "ask": float(q.get("ap") or 0),
                                              "iv": snap.get("impliedVolatility"), "delta": g.get("delta")}
                token = js.get("next_page_token")
                if not token:
                    break
    except Exception as ex:                                   # no quotes today: the model takes over
        print(f"  ! Alpaca options: {ex}")
        return None
    return out or None


def parse_occ(occ: str):
    """SPY261218C00680000 -> ("2026-12-18", "C", 680.0)"""
    tail = occ[-15:]
    try:
        d = datetime.strptime(tail[:6], "%y%m%d").date().isoformat()
        return d, tail[6], int(tail[7:]) / 1000
    except ValueError:
        return None


class Pricer:
    """Bid/ask/mid for a leg: the real quote when there is a usable one, else the model."""
    def __init__(self, today: date, S: float, vix: float, chain: dict | None):
        self.today, self.S, self.atm, self.chain = today, S, M.ATM_VIX * vix / 100, chain

    def model_mid(self, t, K, exp: date):
        T = max((exp - self.today).days, 0) / 365
        return M.bs(self.S, K, T, M.skew_iv(self.atm, self.S, K, T, M.SKEW_INDEX), t)

    def quote(self, t, K, exp: date):
        q = (self.chain or {}).get((exp.isoformat(), t, float(K)))
        if q and q["bid"] > 0 and q["ask"] >= q["bid"]:
            return q["bid"], q["ask"], "quotes"
        m = self.model_mid(t, K, exp)
        h = M.half_spread(m)
        return max(m - h, 0.0), m + h, "model"

    def mid(self, t, K, exp):
        b, a, _ = self.quote(t, K, exp)
        return (a + b) / 2

    def abs_delta(self, leg, K, exp: date):
        q = (self.chain or {}).get((exp.isoformat(), leg["t"], float(K)))
        if self.chain is not None:
            if not q or q["bid"] <= 0:
                return None                                   # no market in this strike
            if q.get("delta") is not None:
                return abs(float(q["delta"]))
        T = max((exp - self.today).days, 1) / 365
        iv = (q or {}).get("iv") or M.skew_iv(self.atm, self.S, K, T, M.SKEW_INDEX)
        return abs(M.greeks(self.S, K, T, float(iv), leg["t"])["delta"])

    def grid(self, exp: date):
        if self.chain:
            ks = sorted({k[2] for k in self.chain if k[0] == exp.isoformat()})
            if len(ks) > 10:
                return ks
        return M.strike_grid(self.S, 1.0)


# ------------------------------------------------------------------ positions
def open_position(sid: str, P: Pricer, today: date, wheel_state: dict | None) -> dict:
    st = M.STRATEGIES[sid]
    near = M.next_expiry(today)
    far = M.far_expiry(near)
    legs = [dict(l) for l in st["legs"]]
    shares = 0.0
    if st.get("wheel") and wheel_state and wheel_state.get("shares"):
        legs, shares = [{"t": "C", "s": -1, "d": 0.30}], wheel_state["shares"]
    elif st["stock"]:
        shares = 100.0 * st["stock"]
    exps = [far if l.get("exp") == "far" else near for l in legs]
    grid = P.grid(near)
    strikes = M.pick_strikes_by(legs, P.S, grid, lambda leg, K: P.abs_delta(leg, K, far if leg.get("exp") == "far" else near))
    src, out_legs, debit = set(), [], 0.0
    for l, K, e in zip(legs, strikes, exps):
        b, a, s = P.quote(l["t"], K, e)
        src.add(s)
        fill = a if l["s"] > 0 else b
        debit += l["s"] * fill
        out_legs.append({"t": l["t"], "s": l["s"], "K": K, "exp": e.isoformat(), "fill": round(fill, 3)})
    costs = M.COMMISSION * len(legs)
    if sid == "cash_secured_put" or (st.get("wheel") and not shares):
        capital = strikes[0] * 100
    elif shares:
        capital = P.S * shares + max(debit, 0) * 100
    elif any(l.get("exp") == "far" for l in legs):
        capital = max(debit, 0.01) * 100 + costs
    else:
        from options_backtest import max_loss_at_expiry
        capital = max_loss_at_expiry(legs, strikes, debit) * 100 + costs
    return {"strategy": sid, "open": today.isoformat(), "expiry": near.isoformat(), "S0": round(P.S, 2),
            "shares": shares, "legs": out_legs, "premium": round(-debit, 3), "costs": round(costs, 2),
            "capital": round(capital, 2), "source": "quotes" if src == {"quotes"} else ("model" if src == {"model"} else "mixed")}


def value_legs(pos: dict, P: Pricer, settle: bool) -> tuple[float, float]:
    """(value per share of the option legs, extra closing costs). On settlement the near legs are
    worth what they pay at SPY's close; other legs are sold at the bid / bought back at the ask."""
    val, costs = 0.0, 0.0
    for l in pos["legs"]:
        e = date.fromisoformat(l["exp"])
        if settle and l["exp"] == pos["expiry"]:
            val += l["s"] * (max(P.S - l["K"], 0) if l["t"] == "C" else max(l["K"] - P.S, 0))
        elif settle:
            b, a, _ = P.quote(l["t"], l["K"], e)
            val += l["s"] * (b if l["s"] > 0 else a)
            costs += M.COMMISSION
        else:
            val += l["s"] * P.mid(l["t"], l["K"], e)
    return val, costs


def pnl_of(pos: dict, P: Pricer, settle: bool) -> float:
    val, extra = value_legs(pos, P, settle)
    contracts = (pos["shares"] / 100) if (pos["shares"] and M.STRATEGIES[pos["strategy"]].get("wheel")) else 1.0
    opt = (val + pos["premium"]) * 100 * contracts
    return opt - (pos["costs"] + extra) * math.ceil(contracts - 1e-9) + pos["shares"] * (P.S - pos["S0"])


# ------------------------------------------------------------------ main
def main(demo: bool, out: Path) -> None:
    ledger = json.loads(out.read_text()) if out.exists() else {}
    closes, vix = spy_and_vix(demo)
    days = sorted(closes)
    today = days[-1]
    ledger.setdefault("start", today.isoformat())
    ledger.setdefault("underlying", UNDERLYING)
    ledger.setdefault("trades", [])
    ledger.setdefault("open", {})
    ledger.setdefault("wheel", {})
    S = closes[today]
    exps = [M.next_expiry(today), M.far_expiry(M.next_expiry(today))]
    exps += [date.fromisoformat(p["expiry"]) for p in ledger["open"].values()]
    exps += [date.fromisoformat(l["exp"]) for p in ledger["open"].values() for l in p["legs"]]
    chain = None if demo else alpaca_chain(exps, S)
    print(f"  {today} SPY {S:.2f} VIX {vix:.1f} quotes: {'yes' if chain else 'no (model)'}")

    for sid in M.ORDER:
        pos = ledger["open"].get(sid)
        if pos and date.fromisoformat(pos["expiry"]) <= today:
            # settle on SPY's close on the expiry day (or the last session before it)
            exp = date.fromisoformat(pos["expiry"])
            sday = max(d for d in days if d <= exp)
            P = Pricer(sday, closes[sday], vix, chain if sday == today else None)
            pnl = pnl_of(pos, P, settle=True)
            how = "expiry"
            if M.STRATEGIES[sid].get("wheel"):
                leg = pos["legs"][0]
                if leg["t"] == "P" and P.S < leg["K"]:
                    ledger["wheel"] = {"shares": 100.0, "basis": leg["K"]}; how = "assigned"
                elif leg["t"] == "C" and P.S > leg["K"]:
                    ledger["wheel"] = {}; how = "called away"
            closed = {**pos, "close": sday.isoformat(), "S1": round(P.S, 2), "pnl": round(pnl, 2),
                      "ret": round(100 * pnl / pos["capital"], 2), "how": how}
            closed.pop("mark", None)
            ledger["trades"].append(closed)
            del ledger["open"][sid]
            pos = None
            print(f"  closed {sid}: {closed['pnl']:+.2f}")
        P = Pricer(today, S, vix, chain)
        if not pos and M.next_expiry(today) > today:
            pos = open_position(sid, P, today, ledger["wheel"] if M.STRATEGIES[sid].get("wheel") else None)
            if M.STRATEGIES[sid].get("wheel") and ledger["wheel"].get("shares"):
                pos["S0"] = round(S, 2)
            ledger["open"][sid] = pos
            print(f"  opened {sid}: {[(l['t'], l['s'], l['K']) for l in pos['legs']]} premium {pos['premium']} ({pos['source']})")
        u = pnl_of(pos, P, settle=False)
        pos["mark"] = {"date": today.isoformat(), "S": round(S, 2), "pnl": round(u, 2), "ret": round(100 * u / pos["capital"], 2)}

    ledger["as_of"] = today.isoformat()
    ledger["spot"] = {"S": round(S, 2), "vix": round(vix, 2)}     # the Options page's fallback when live quotes are down
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger, indent=1))
    print(f"wrote {out}: {len(ledger['trades'])} closed, {len(ledger['open'])} open")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    if a.demo and not a.out:
        sys.exit("--demo needs --out (never write simulated trades into the real ledger)")
    main(a.demo, a.out or OUT)
