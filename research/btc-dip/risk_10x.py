"""5% dip / 48h hold since 2023 on a $100k account, 10x leverage, isolated margin = X% of the account.
With isolated margin the most a trade can lose is its margin, so margin 1% = risk 1% per trade."""
import json, numpy as np, pandas as pd
from backtest import load, run
px,f=load()
fh=pd.Series(0.0,index=px.index); fh[px.index.hour.isin([0,8,16])]=0.0001
ix=f.index.intersection(fh.index); fh.loc[ix]=f.reindex(ix)
sub=px.loc["2023-01-01":]
LEV, FEE, START = 10, 0.0005, 100_000
t=run(sub,dip=0.05,lookback=24,hold=48,lev=LEV,funding=fh,cost=FEE)
last_open = t.iloc[-1].hours < 48 and t.iloc[-1].reason=="time"
def sim(margin_pct):
    eq=START; rows=[]
    for k,r in t.iterrows():
        m=eq*margin_pct; pnl=m*r.ret; open_=(k==len(t)-1 and last_open)
        rows.append(dict(eq_before=eq,margin=m,notional=m*LEV,pnl=pnl,acct_ret=pnl/eq,open=open_))
        if not open_: eq+=pnl
    return pd.DataFrame(rows), eq
res={}
for mp in [0.01,0.02,0.05,0.10]:
    d,eq=sim(mp); c=d[~d.open]
    curve=START+c.pnl.cumsum(); dd=(curve/np.maximum.accumulate(np.r_[START,curve.values][1:].clip(min=START) if False else curve.cummax().clip(lower=START))-1).min()
    res[mp]=dict(end=eq,total=eq/START-1,maxdd=float(dd),worst_usd=float(c.pnl.min()),best_usd=float(c.pnl.max()),liq=int((t.reason=="liquidated").sum()))
    print(f"margin {mp:.0%}: end ${eq:,.0f} ({eq/START-1:+.1%}), maxDD {dd:.1%}, worst ${c.pnl.min():,.0f}, best ${c.pnl.max():,.0f}")
print("trades",len(t),"liquidations",(t.reason=="liquidated").sum(),"win",(t[:-1].ret>0).mean() if last_open else (t.ret>0).mean())
print(t[t.reason=="liquidated"][["entry_time","entry","gross","ret"]])
d,eq=sim(0.01)
idx=sub.index; trades=[]
for k,r in t.iterrows():
    a=idx.get_loc(r.entry_time); b=idx.get_loc(r.exit_time); s=max(0,a-24); e=min(len(sub)-1,b+12); w=sub.iloc[s:e+1]
    dr=d.iloc[k]
    trades.append(dict(n=k+1,et=r.entry_time.strftime("%Y-%m-%d %H:%M"),entry=round(r.entry,2),exit=round(r.exit,2),high=round(r.dip_from,2),
      liqpx=round(r.entry*(1-1/LEV+0.005),2),reason=r.reason,ret=round(r.ret,5),gross=round(r.gross,5),hours=int(r.hours),open=bool(dr.open),
      eq=round(dr.eq_before,2),margin=round(dr.margin,2),notional=round(dr.notional,2),pnl=round(dr.pnl,2),
      ei=a-s,xi=b-s,o=w.open.round(1).tolist(),h=w.high.round(1).tolist(),l=w.low.round(1).tolist(),c=w.close.round(1).tolist()))
out=dict(trades=trades,variants={f"{int(k*100)}":v for k,v in res.items()},start=START)
json.dump(out,open("/tmp/claude-0/-home-claude/f7b237c4-8c48-50ee-827e-d9f620ff571a/scratchpad/risk10x.json","w"))
