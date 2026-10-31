import itertools, numpy as np, pandas as pd
from backtest import load, run, stats
px, f = load()
# funding: fill missing hours before 2020 / after archive end with a typical 0.01% per 8h
fh = pd.Series(0.0, index=px.index)
stamp = px.index.hour.isin([0, 8, 16])
fh[stamp] = 0.0001
fh.loc[f.index.intersection(fh.index)] = f.reindex(f.index.intersection(fh.index))
FUND = fh
YEARS = (px.index[-1] - px.index[0]).days / 365.25
rows = []
for dip, lb, hold in itertools.product([0.03,0.04,0.05,0.06,0.07,0.08,0.10],[6,12,24,48,72],[1,2,3,4,6,8,12,18,24,36,48,72,96,120]):
    t = run(px, dip=dip, lookback=lb, hold=hold, cost=0.001)
    s = stats(t, YEARS); s.update(dip=dip, lookback=lb, hold=hold)
    for name, a, b in [("is", None, "2022-12-31"), ("oos", "2023-01-01", None)]:
        tt = t[(t.entry_time >= pd.Timestamp(a, tz="UTC")) if a else t.entry_time.notna()]
        if b: tt = tt[tt.entry_time <= pd.Timestamp(b, tz="UTC")]
        s[f"{name}_avg"] = tt.ret.mean() if len(tt) else np.nan
        s[f"{name}_n"] = len(tt)
        s[f"{name}_win"] = (tt.ret > 0).mean() if len(tt) else np.nan
    rows.append(s)
g = pd.DataFrame(rows); g.to_csv("data/grid.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
cols = ["dip","lookback","hold","trades","per_year","win","avg","median","total","cagr","maxdd","pf","is_avg","oos_avg","oos_n","oos_win"]
print("== 5% dip, 24h lookback, by hold ==")
print(g[(g.dip==0.05)&(g.lookback==24)][cols].round(4).to_string(index=False))
print("== 5% dip, by lookback & hold: avg trade (after 0.2% round-trip) ==")
print(g[g.dip==0.05].pivot(index="hold", columns="lookback", values="avg").mul(100).round(2))
print("== 24h lookback, by dip & hold: avg trade % ==")
print(g[g.lookback==24].pivot(index="hold", columns="dip", values="avg").mul(100).round(2))
print("== OOS (2023+) avg % 24h lookback ==")
print(g[g.lookback==24].pivot(index="hold", columns="dip", values="oos_avg").mul(100).round(2))
print("== OOS n ==")
print(g[g.lookback==24].pivot(index="hold", columns="dip", values="oos_n"))
