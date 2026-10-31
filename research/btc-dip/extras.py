import numpy as np, pandas as pd
from backtest import load, run, stats
px,f=load(); pd.set_option("display.width",250); pd.set_option("display.max_columns",30)
# regime filter: price vs 200-day average (4800 hourly bars), known at entry
sma=px.close.rolling(4800).mean().shift(1)
up=(px.close.shift(1)>sma)
print("== Trend filter (only buy dips above the 200-day average) — 5% dip, 24h lookback ==")
rows={}
for hold in [4,12,24,36,48,72,96]:
    t=run(px,dip=0.05,lookback=24,hold=hold)
    t["up"]=up.reindex(t.entry_time).to_numpy()
    for k,g in [("above",t[t.up==True]),("below",t[t.up==False])]:
        o=g[g.entry_time>="2023"]
        rows[(hold,k)]=dict(n=len(g),avg=g.ret.mean()*100,win=(g.ret>0).mean()*100,o_n=len(o),o_avg=o.ret.mean()*100)
print(pd.DataFrame(rows).T.round(2))
print("\n== Take profit / stop loss — 5% dip, 24h lookback, max hold 48h ==")
rows={}
YEARS=(px.index[-1]-px.index[0]).days/365.25
for tp in [None,0.02,0.03,0.05,0.08]:
  for sl in [None,0.03,0.05,0.08]:
    t=run(px,dip=0.05,lookback=24,hold=48,tp=tp,sl=sl); o=t[t.entry_time>="2023"]
    rows[(tp,sl)]=dict(avg=t.ret.mean()*100,win=(t.ret>0).mean()*100,o_avg=o.ret.mean()*100,o_win=(o.ret>0).mean()*100,maxdd=stats(t,YEARS)["maxdd"]*100)
print(pd.DataFrame(rows).T.round(2))
