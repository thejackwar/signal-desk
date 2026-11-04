"""
VIX Seasonality: when in the year the VIX (the S&P 500's "fear gauge") tends to be high or low,
and whether using that would have helped someone holding the S&P 500.

One measure is used everywhere: the VIX divided by its average over the past year (252 sessions),
so 1.20 means "20% above its past-year average". Using only past data, it also works for this year.

Writes docs/data/vix.json:
  - now        today's VIX, its seasonal normal for this week and what usually comes next
  - curve      the typical path through the year (median and middle half, by week) and this year's
  - months     each month: VIX vs its past-year average, how often the VIX rose, the S&P 500's return
  - weekdays   the VIX's daily change by day of the week
  - holidays   the VIX into and out of market holidays (Thanksgiving, Christmas, ...)
  - elections  the VIX around US election days, midterms and presidential, and the S&P 500 after
  - rules      three filters for an S&P 500 holder, tested against simply holding, out of sample:
                 calendar  skip the 3 months that were the most volatile in the years before
                 seasonal  T-bills when the VIX is 25%+ above its normal level for the time of year
                 plain     the same without the seasonal adjustment (does seasonality add anything?)
               with the same Italian-tax account as the Smaller Crashes page, and a threshold grid
  - gate       the two filter states day by day (as changes), for the Strategy Lab's VIX filter

Rules are fixed in advance. The seasonal "normal" and the calendar's months only use earlier years.
Sends a phone notification (ntfy topic <NTFY_PREFIX>-vix) when the seasonal rule switches.

  python engine/vix_season.py           # real prices (Yahoo: ^VIX, SPY, ^IRX)
  python engine/vix_season.py --demo    # random prices, offline
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "vix.json"
YEAR, MIN_YEARS = 252, 5                      # past-year average; years of history before a seasonal estimate
TH, GRID = 1.25, [1.10, 1.25, 1.50, 1.75]     # the rule's threshold, and the ones shown next to it
SKIP = 3                                      # months the calendar rule skips
COST, TAX, TAX_BILLS, START = 0.001, 0.26, 0.125, 10_000
CRASHES = [("Dot-com bust", "2000-03-24", "2002-10-09"), ("Financial crisis", "2007-10-09", "2009-03-09"),
           ("2011 debt ceiling", "2011-04-29", "2011-10-03"), ("Late-2018 sell-off", "2018-09-20", "2018-12-24"),
           ("Covid crash", "2020-02-19", "2020-03-23"), ("2022 bear market", "2022-01-03", "2022-10-12"),
           ("2025 tariff drop", "2025-02-19", "2025-04-08")]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ------------------------------------------------------------------ data
def fetch(demo: bool):
    if demo:
        rng = np.random.default_rng(7)
        idx = pd.bdate_range("1990-01-02", "2026-11-03")
        doy = idx.dayofyear.values
        season = 1 + 0.12 * np.exp(-((doy - 285) / 40.0) ** 2) - 0.08 * np.exp(-((doy - 355) / 12.0) ** 2)
        lv, x = [], np.log(19.0)
        for i in range(len(idx)):
            x += 0.03 * (np.log(19.0 * season[i]) - x) + rng.normal(0, 0.06)
            lv.append(np.exp(x))
        vix = pd.Series(lv, index=idx)
        r = rng.normal(0.0004, 0.012, len(idx)) - 0.002 * (vix.pct_change().fillna(0).values)
        spy = pd.Series(30 * np.exp(np.cumsum(r)), index=idx)
        spy = spy[spy.index >= "1993-01-29"]
        return vix, spy, pd.Series(3.0, index=spy.index)
    import yfinance as yf
    raw = yf.download(["^VIX", "SPY", "^IRX"], start="1989-12-01", interval="1d", auto_adjust=True, progress=False)["Close"]
    raw.index = pd.DatetimeIndex(raw.index).tz_localize(None)
    vix, spy = raw["^VIX"].dropna(), raw["SPY"].dropna()
    return vix, spy, raw["^IRX"].reindex(spy.index).ffill()


def r1(x, d=1):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


def week_of(idx: pd.DatetimeIndex) -> np.ndarray:
    """Week of the year 1..52 (day of year / 7, the 53rd folded into the 52nd)."""
    return np.minimum((idx.dayofyear.values - 1) // 7 + 1, 52)


# -------------------------------------------------------- seasonal levels
def relative(vix: pd.Series) -> pd.Series:
    """VIX / its average over the past 252 sessions (that day included)."""
    return vix / vix.rolling(YEAR, min_periods=YEAR).mean()


def weekly_table(rel: pd.Series) -> pd.DataFrame:
    """Rows = years, columns = week 1..52: the average relative VIX that week."""
    df = pd.DataFrame({"y": rel.index.year, "w": week_of(rel.index), "v": rel.values}).dropna()
    return df.groupby(["y", "w"])["v"].mean().unstack()


def seasonal_factor(rel: pd.Series) -> pd.Series:
    """For each day, the median relative VIX of that week of the year in EARLIER years only
    (at least MIN_YEARS of them; otherwise 1). Smoothed over neighbouring weeks."""
    tab = weekly_table(rel)
    years = sorted(tab.index)
    fac = {}
    for y in sorted(set(rel.index.year)):
        past = tab.loc[[x for x in years if x < y]]
        past = past[past.count(axis=1) >= 40]                    # full years only
        if len(past) < MIN_YEARS:
            fac[y] = np.ones(53)
            continue
        med = past.median().reindex(range(1, 53)).values
        sm = np.array([np.nanmean(med[[(w - 1) % 52, w, (w + 1) % 52]]) for w in range(52)])  # 3-week smoothing, wraps
        fac[y] = np.concatenate([[1.0], sm])
    w = week_of(rel.index)
    return pd.Series([fac[y][k] for y, k in zip(rel.index.year, w)], index=rel.index)


def skip_months(rel: pd.Series) -> dict[int, list[int]]:
    """For each year, the SKIP months with the highest average relative VIX in the years before it."""
    m = pd.DataFrame({"y": rel.index.year, "m": rel.index.month, "v": rel.values}).dropna()
    tab = m.groupby(["y", "m"])["v"].mean().unstack()
    out = {}
    for y in sorted(set(rel.index.year)) + [max(rel.index.year) + 1]:
        past = tab.loc[[x for x in tab.index if x < y]]
        past = past[past.count(axis=1) == 12]
        out[y] = [] if len(past) < MIN_YEARS else sorted(int(k) for k in past.mean().nlargest(SKIP).index)
    return out


# ----------------------------------------------------------- the account
def account(c: pd.Series, irx: pd.Series, pos: pd.Series):
    """Daily returns of holding SPY when pos is True, T-bills otherwise, 10 bp per switch,
    plus the same account after Italian tax (as engine/crash_filter.py)."""
    r = c.pct_change().fillna(0)
    bill = (irx.fillna(0) / 100 / 252).clip(lower=0)
    switch = pos != pos.shift(1).fillna(pos.iloc[0])
    rule = pd.Series(np.where(pos.values, r.values, bill.values) - switch.values * COST, index=c.index)
    v, basis, interest, after = START, START, 0.0, []
    for i in range(len(c)):
        if switch.iloc[i]:
            if not pos.iloc[i]:
                v -= max(0.0, v - basis) * TAX
            else:
                v -= max(0.0, interest) * TAX_BILLS
                interest, basis = 0.0, v
        g = v * rule.iloc[i]
        if not pos.iloc[i]:
            interest += g
        v += g
        after.append(v)
    after[-1] = v - (max(0.0, v - basis) * TAX if pos.iloc[-1] else max(0.0, interest) * TAX_BILLS)
    return rule, r, switch, after[-1]


def stats(ret: pd.Series) -> dict:
    eq = (1 + ret).cumprod()
    yrs, sd = len(ret) / 252, ret.std()
    return {"cagr": r1((eq.iloc[-1] ** (1 / yrs) - 1) * 100, 2), "vol": r1(sd * np.sqrt(252) * 100),
            "sharpe": r1(ret.mean() / sd * np.sqrt(252), 2), "maxdd": r1((eq / eq.cummax() - 1).min() * 100)}


def window_dd(ret: pd.Series, a: str, b: str):
    s = ret[(ret.index >= a) & (ret.index <= b)]
    if len(s) < 5:
        return None
    eq = (1 + s).cumprod()
    return r1((eq / eq.cummax() - 1).min() * 100)


def weekly_decision(flag_ok: pd.Series) -> pd.Series:
    """Checked at each week's last close, held from the next session (as Smaller Crashes)."""
    d = flag_ok.index.to_series()
    last = d.groupby(flag_ok.index.to_period("W-FRI")).transform("max") == d
    return flag_ok.where(last.values).ffill().shift(1).fillna(True).astype(bool)


def rule_doc(name, desc, c, irx, pos, hold_cagr_after=None):
    rule, r, switch, after = account(c, irx, pos)
    yrs = len(c) / 252
    sw = [{"date": f"{d:%Y-%m-%d}", "to": "spy" if pos.loc[d] else "bills", "close": r1(c.loc[d], 2)}
          for d in c.index[switch.values] if d != c.index[0]]
    eq = START * (1 + rule).cumprod()
    return rule, {
        "id": name, "rule": desc, "stats": stats(rule), "invested": r1(pos.mean() * 100),
        "switches": len(sw), "per_year": r1(len(sw) / yrs), "after_tax": r1(((after / START) ** (1 / yrs) - 1) * 100, 2),
        "crashes": [window_dd(rule, a, b) for _, a, b in CRASHES],
        "years": {int(y): r1(((1 + rule[rule.index.year == y]).prod() - 1) * 100) for y in sorted(set(c.index.year))},
        "last_switches": sw[-8:][::-1], "position": "spy" if pos.iloc[-1] else "bills",
        "since": sw[-1]["date"] if sw else f"{c.index[0]:%Y-%m-%d}", "eq": eq,
    }


# --------------------------------------------------------------- holidays
def holiday_name(gap: pd.Timestamp) -> str:
    m, d, wd = gap.month, gap.day, gap.weekday()
    if m == 11 and wd == 3 and 22 <= d <= 28:
        return "Thanksgiving"
    if m == 12 and 24 <= d <= 26:
        return "Christmas"
    if (m == 12 and d == 31) or (m == 1 and d <= 2):
        return "New Year"
    if m == 7 and 3 <= d <= 5:
        return "Independence Day"
    if m == 9 and wd == 0 and d <= 7:
        return "Labor Day"
    if m == 5 and wd == 0 and d >= 25:
        return "Memorial Day"
    if m in (3, 4) and wd == 4:
        return "Good Friday"
    return "Other"


def holidays(vix: pd.Series) -> dict:
    idx = vix.index
    chg = lambda a, b: (vix.iloc[b] / vix.iloc[a] - 1) * 100
    rows = []
    for i in range(3, len(idx) - 3):
        nxt = idx[i] + pd.offsets.BDay(1)
        if idx[i + 1] > nxt:                                   # a weekday with no session: a market holiday
            name = holiday_name(nxt)
            if name == "Christmas" and rows and rows[-1][0] == "New Year":
                continue
            rows.append((name, chg(i - 3, i), chg(i, i + 2)))
    df = pd.DataFrame(rows, columns=["h", "into", "after"])
    base_in = ((vix / vix.shift(3) - 1) * 100).dropna()
    base_af = ((vix.shift(-2) / vix - 1) * 100).dropna()
    order = ["Thanksgiving", "Christmas", "New Year", "Independence Day", "Labor Day", "Memorial Day", "Good Friday"]
    out = [{"name": h, "n": int(len(g)), "into": r1(g["into"].median()), "into_down": r1((g["into"] < 0).mean() * 100, 0),
            "after": r1(g["after"].median()), "after_up": r1((g["after"] > 0).mean() * 100, 0)}
           for h in order for g in [df[df.h == h]] if len(g) >= 5]
    return {"list": out, "all": {"into": r1(df["into"].median()), "into_down": r1((df["into"] < 0).mean() * 100, 0),
                                 "after": r1(df["after"].median()), "after_up": r1((df["after"] > 0).mean() * 100, 0), "n": int(len(df))},
            "normal": {"into": r1(base_in.median()), "into_down": r1((base_in < 0).mean() * 100, 0),
                       "after": r1(base_af.median()), "after_up": r1((base_af > 0).mean() * 100, 0)}}


# -------------------------------------------------------------- elections
def election_day(y: int) -> date:
    d = date(y, 11, 2)                 # the Tuesday after the first Monday of November
    while d.weekday() != 1:
        d += timedelta(days=1)
    return d


def elections(vix: pd.Series, spy: pd.Series) -> dict:
    PRE, POST = 30, 40
    paths = {"midterm": [], "presidential": []}
    spy_after = {"midterm": [], "presidential": []}
    current = None
    for y in range(vix.index[0].year, vix.index[-1].year + 1, 1):
        if y % 2:
            continue
        kind = "presidential" if y % 4 == 0 else "midterm"
        e = pd.Timestamp(election_day(y))
        k = vix.index.searchsorted(e)
        if k >= len(vix) or k < PRE + 1:
            continue
        base = vix.iloc[k - 1]
        seg = vix.iloc[k - PRE:k + POST + 1] / base * 100
        if k + POST < len(vix):
            paths[kind].append(seg.values)
        if y == vix.index[-1].year and e >= vix.index[-1] - pd.Timedelta(days=120):
            current = {"year": y, "kind": kind, "date": f"{e:%Y-%m-%d}", "path": [r1(x) for x in seg.values]}
        ks = spy.index.searchsorted(e)
        if 0 < ks < len(spy):
            c0 = spy.iloc[ks - 1]
            spy_after[kind].append([((spy.iloc[ks + h] / c0 - 1) * 100) if ks + h < len(spy) else np.nan for h in (20, 60, 120, 250)])
    out = {"pre": PRE, "post": POST, "current": current}
    for kind in paths:
        P = np.array(paths[kind])
        A = np.array(spy_after[kind]) if spy_after[kind] else np.empty((0, 4))
        out[kind] = {"n": len(P), "median": [r1(x) for x in np.median(P, axis=0)] if len(P) else [],
                     "spy": [{"h": h, "median": r1(np.nanmedian(A[:, j])), "up": r1(np.nanmean(A[:, j] > 0) * 100, 0),
                              "n": int(np.sum(~np.isnan(A[:, j])))} for j, h in enumerate((20, 60, 120, 250))] if len(A) else []}
    return out


def notify(prefix: str, now: dict):
    to_bills = now["position"] == "bills"
    title = "VIX filter: moving to T-bills" if to_bills else "VIX filter: back in the S&P 500"
    msg = (f"VIX {now['vix']:.1f} closed the week {abs(now['vs_normal']):.0f}% {'above' if now['vs_normal'] >= 0 else 'below'} "
           f"its normal level for this time of year ({now['normal']:.1f}). The seasonal rule "
           f"{'sells the S&P 500 and holds T-bills' if to_bills else 'buys back the S&P 500'} at the next session.")
    req = urllib.request.Request(f"https://ntfy.sh/{prefix}-vix", data=msg.encode(), method="POST",
                                 headers={"Title": title, "Tags": "chart_with_upwards_trend"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"  ! notification failed: {e}")


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    vix, spy, irx = fetch(args.demo)
    if len(vix) < 15 * 252 or len(spy) < 10 * 252:
        raise SystemExit("Not enough VIX/SPY data; leaving the previous file untouched.")

    rel = relative(vix)
    fac = seasonal_factor(rel)
    avg = vix.rolling(YEAR, min_periods=YEAR).mean()
    normal = avg * fac
    ratio_s, ratio_p = vix / normal, vix / avg
    skips = skip_months(rel)
    cal_ok = pd.Series([m not in skips[y] for y, m in zip(vix.index.year, vix.index.month)], index=vix.index)

    # ---- descriptive: the year, month by month (full years with a past-year average)
    tab = weekly_table(rel)
    full = tab[tab.count(axis=1) >= 48]
    this_y = vix.index[-1].year
    curve = {"weeks": list(range(1, 53)),
             "median": [r1(x, 3) for x in full.median().reindex(range(1, 53))],
             "p25": [r1(x, 3) for x in full.quantile(0.25).reindex(range(1, 53))],
             "p75": [r1(x, 3) for x in full.quantile(0.75).reindex(range(1, 53))],
             "this_year": [r1(x, 3) for x in tab.loc[this_y].reindex(range(1, 53))] if this_y in tab.index else [],
             "years": f"{full.index.min()}–{full.index.max()}", "n": int(len(full))}

    mlev = pd.DataFrame({"y": rel.index.year, "m": rel.index.month, "rel": rel.values, "vix": vix.values}).dropna()
    mm = mlev.groupby(["y", "m"]).mean()
    me = vix.groupby([vix.index.year, vix.index.month]).last()          # month-end VIX
    mchg = (me / me.shift(1) - 1) * 100
    se = spy.groupby([spy.index.year, spy.index.month]).last()
    sret = (se / se.shift(1) - 1) * 100
    months = []
    for m in range(1, 13):
        rr = mm.xs(m, level="m")["rel"]
        vc = mchg.xs(m, level=1).dropna()
        sr = sret.xs(m, level=1).dropna()
        months.append({"m": MONTHS[m - 1], "rel": r1(rr.mean(), 3), "rel_med": r1(rr.median(), 3),
                       "above": r1((rr > 1).mean() * 100, 0), "vix": r1(mm.xs(m, level="m")["vix"].mean()),
                       "vix_up": r1((vc > 0).mean() * 100, 0), "vix_chg": r1(vc.median()),
                       "spy": r1(sr.mean(), 2), "spy_med": r1(sr.median(), 2), "spy_up": r1((sr > 0).mean() * 100, 0),
                       "n": int(len(rr)), "n_spy": int(len(sr))})

    # ---- weekdays: the VIX's daily change
    dch = (vix.pct_change() * 100).dropna()
    weekdays = [{"d": n, "mean": r1(g.mean(), 2), "median": r1(g.median(), 2), "up": r1((g > 0).mean() * 100, 0), "n": int(len(g))}
                for k, n in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]) for g in [dch[dch.index.weekday == k]]]

    # ---- rules, on the SPY sessions (from SPY's first day with a seasonal estimate behind it)
    c = spy[spy.index >= max(spy.index[0], normal.first_valid_index())]
    i_rx = irx.reindex(c.index).ffill()
    al = lambda s: s.reindex(c.index).ffill()
    hold_pos = pd.Series(True, index=c.index)
    _, R_hold, _, after_hold = account(c, i_rx, hold_pos)
    yrs = len(c) / 252
    seasonal_ok = al(ratio_s <= TH).fillna(True).astype(bool)
    plain_ok = al(ratio_p <= TH).fillna(True).astype(bool)
    rules, eqs = [], {}
    for rid, desc, pos in [
        ("calendar", f"Hold the S&P 500, except in the {SKIP} calendar months whose VIX was highest (vs its past-year average) in all the years "
                     f"before; T-bills then. Sell at the close before those months start, buy back at the close before they end. 10 bp per switch.",
         al(cal_ok).fillna(True).astype(bool)),
        ("seasonal", f"Every Friday close: VIX more than {round((TH - 1) * 100)}% above its normal level for that week of the year → T-bills "
                     f"from the next session; otherwise the S&P 500. Normal = its past-year average × how high the VIX usually was that "
                     f"week in earlier years. 10 bp per switch.", weekly_decision(seasonal_ok)),
        ("plain", f"The same, but VIX vs its past-year average only, with no seasonal adjustment: does the seasonality add anything?",
         weekly_decision(plain_ok)),
    ]:
        R, d = rule_doc(rid, desc, c, i_rx, pos)
        eqs[rid] = d.pop("eq")
        rules.append(d)

    grid = []
    for kind, ratio in (("seasonal", ratio_s), ("plain", ratio_p)):
        for th in GRID:
            pos = weekly_decision(al(ratio <= th).fillna(True).astype(bool))
            R, _, sw, _ = account(c, i_rx, pos)
            grid.append({"kind": kind, "th": th, **stats(R), "invested": r1(pos.mean() * 100), "switches": int(sw.sum() - (1 if sw.iloc[0] else 0))})

    eq_hold = START * (1 + R_hold).cumprod()
    wk = eq_hold.groupby(eq_hold.index.to_period("W-FRI")).tail(1).index
    weekly = {"dates": [f"{d:%Y-%m-%d}" for d in wk], "hold": [round(float(x)) for x in eq_hold.reindex(wk)],
              **{k: [round(float(x)) for x in v.reindex(wk)] for k, v in eqs.items()}}

    # ---- today, and what usually comes next from this week of the year
    last = vix.index[-1]
    w_now = int(week_of(pd.DatetimeIndex([last]))[0])
    nxt20 = (vix.shift(-20) / vix - 1) * 100
    spy20 = (spy.shift(-20) / spy - 1) * 100
    same_w = week_of(vix.index) == w_now
    hist = nxt20[same_w & (vix.index.year < this_y)].dropna()
    by_year = hist.groupby(hist.index.year).median()
    s_same = spy20[(week_of(spy.index) == w_now) & (spy.index.year < this_y)].dropna()
    s_by_year = s_same.groupby(s_same.index.year).median()
    is_week_end = last.weekday() == 4
    seas = next(r for r in rules if r["id"] == "seasonal")
    next_pos = ("spy" if bool(seasonal_ok.iloc[-1]) else "bills") if is_week_end else seas["position"]
    now = {"date": f"{last:%Y-%m-%d}", "vix": r1(vix.iloc[-1], 2), "avg": r1(avg.iloc[-1], 2), "factor": r1(fac.iloc[-1], 3),
           "normal": r1(normal.iloc[-1], 2), "vs_normal": r1((ratio_s.iloc[-1] - 1) * 100), "vs_avg": r1((ratio_p.iloc[-1] - 1) * 100),
           "pctile": r1((vix <= vix.iloc[-1]).mean() * 100, 0), "week": w_now, "month": MONTHS[last.month - 1],
           "position": next_pos, "week_end": is_week_end, "over": bool(ratio_s.iloc[-1] > TH), "th": round((TH - 1) * 100),
           "since": seas["since"], "skip": [MONTHS[m - 1] for m in skips.get(this_y, [])],
           "skip_next": [MONTHS[m - 1] for m in skips.get(this_y + 1, [])], "in_skip": bool(last.month in skips.get(this_y, [])),
           "ahead": {"years": int(len(by_year)), "vix_med": r1(by_year.median()), "vix_down": r1((by_year < 0).mean() * 100, 0),
                     "spy_med": r1(s_by_year.median(), 2), "spy_up": r1((s_by_year > 0).mean() * 100, 0)},
           "bill_rate": r1(irx.iloc[-1], 2) if pd.notna(irx.iloc[-1]) else None}

    # ---- the Strategy Lab gate, daily, as the days the states change: [date, outside skip months, VIX not over its normal]
    ok_s = (ratio_s <= TH) | ratio_s.isna()
    st = pd.DataFrame({"c": cal_ok.astype(int), "n": ok_s.astype(int)})
    ch = st.ne(st.shift()).any(axis=1)
    gate = {"th": round((TH - 1) * 100), "changes": [[f"{d:%Y-%m-%d}", int(a), int(b)] for d, (a, b) in st[ch].iterrows()],
            "skip": {str(y): [MONTHS[m - 1] for m in v] for y, v in skips.items() if v}}

    prev = json.loads(OUT.read_text()) if OUT.exists() else None
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "demo": args.demo,
           "vix_start": f"{vix.index[0]:%Y-%m-%d}", "start": f"{c.index[0]:%Y-%m-%d}", "now": now, "curve": curve,
           "months": months, "weekdays": weekdays, "holidays": holidays(vix), "elections": elections(vix, spy),
           "hold": {"stats": stats(R_hold), "after_tax": r1(((after_hold / START) ** (1 / yrs) - 1) * 100, 2),
                    "crashes": [window_dd(R_hold, a, b) for _, a, b in CRASHES],
                    "years": {int(y): r1(((1 + R_hold[R_hold.index.year == y]).prod() - 1) * 100) for y in sorted(set(c.index.year))}},
           "crashes": [{"name": n, "from": a, "to": b} for n, a, b in CRASHES],
           "rules": rules, "grid": grid, "weekly": weekly, "gate": gate}
    OUT.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False))

    s = {r["id"]: r["stats"] for r in rules}
    print(f"VIX {now['vix']} ({now['vs_normal']:+.0f}% vs seasonal normal {now['normal']}); "
          f"hold {doc['hold']['stats']['cagr']}%/yr worst {doc['hold']['stats']['maxdd']}% | "
          + " | ".join(f"{k} {v['cagr']}%/yr worst {v['maxdd']}%" for k, v in s.items()) + f" -> {OUT.relative_to(ROOT)}")
    print("Months (VIX vs past-year avg): " + ", ".join(f"{m['m']} {m['rel']:.2f}" for m in months))

    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if prefix and prev and not args.demo and prev.get("now", {}).get("position") != now["position"]:
        notify(prefix, now)


if __name__ == "__main__":
    main()
