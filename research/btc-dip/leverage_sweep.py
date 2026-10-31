"""5% dip / 48h since 2023, $100k, 1% of the account as isolated margin per trade, leverage 1x-50x."""
import json, numpy as np, pandas as pd
from backtest import load, run
px,f=load()
fh=pd.Series(0.0,index=px.index); fh[px.index.hour.isin([0,8,16])]=0.0001
ix=f.index.intersection(fh.index); fh.loc[ix]=f.reindex(ix)
sub=px.loc["2023-01-01":]; START=100_000
rows=[]; per_lev={}
for lev in [1,2,3,5,10,15,20,25,50]:
    t=run(sub,dip=0.05,lookback=24,hold=48,lev=max(lev,1.0001) if lev>1 else 1,funding=fh if lev>1 else None,cost=0.0005)
    if lev==1:  # 1x on a perp: no liquidation risk to speak of; charge funding manually via lev path
        t=run(sub,dip=0.05,lookback=24,hold=48,lev=1.0,cost=0.0005)
    op=(t.iloc[-1].hours<48)&(t.iloc[-1].reason=="time")
    c=t.iloc[:-1] if op else t
    eq=START; curve=[START]; pnls=[]
    for r in c.ret: p=eq*0.01*r; pnls.append(p); eq+=p; curve.append(eq)
    cu=np.array(curve); dd=(cu/np.maximum.accumulate(cu)-1).min()
    rows.append(dict(lev=lev,trades=len(c),liq=int((c.reason=="liquidated").sum()),win=float((c.ret>0).mean()),end=eq,total=eq/START-1,maxdd=float(dd),
                     avg_win=float(np.mean([p for p in pnls if p>0])),avg_loss=float(np.mean([p for p in pnls if p<=0])),liq_dist=(1/lev-0.005) if lev>1 else 1.0))
    per_lev[lev]=t
d=pd.DataFrame(rows); pd.set_option("display.width",200); print(d.round(3).to_string(index=False))
d.to_csv("data/leverage_sweep.csv",index=False)
# per-trade comparison for latest 12 trades (entries present in the 1x run)
base=per_lev[1]; keep=[3,10,20,50]; out=[]
idx=sub.index
for k in range(len(base)-12,len(base)):
    r=base.iloc[k]; a=idx.get_loc(r.entry_time); b=a+48 if a+48<len(sub) else len(sub)-1
    s=max(0,a-24); e=min(len(sub)-1,b+12); w=sub.iloc[s:e+1]
    res={}
    for L in keep:
        tl=per_lev[L]; m=tl[tl.entry_time==r.entry_time]
        if len(m): mm=m.iloc[0]; res[L]=dict(pnl=round(1000*mm.ret), liq=mm.reason=="liquidated", liqpx=round(r.entry*(1-1/L+0.005)), xh=int(idx.get_loc(mm.exit_time)-s))
        else: res[L]=None
    kk=3
    bars=[[round(w.open.iloc[i]),round(w.high.iloc[i:i+kk].max()),round(w.low.iloc[i:i+kk].min()),round(w.close.iloc[min(i+kk,len(w))-1])] for i in range(0,len(w),kk)]
    out.append(dict(n=k+1,et=r.entry_time.strftime("%Y-%m-%d %H"),en=round(r.entry),hi=round(r.dip_from),ei=(a-s)//kk,xi=(min(b,idx.get_loc(r.exit_time))-s)//kk,open=bool(r.hours<48),
                    mv=round(r.gross*100,1),res={str(L):(None if v is None else dict(p=v["pnl"],q=v["liq"],lp=v["liqpx"],x=v["xh"]//kk)) for L,v in res.items()},b=bars))
json.dump(dict(sweep=rows,trades=out),open("/tmp/claude-0/-home-claude/f7b237c4-8c48-50ee-827e-d9f620ff571a/scratchpad/sweep.json","w"))
