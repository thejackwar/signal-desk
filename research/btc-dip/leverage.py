import numpy as np, pandas as pd
from backtest import load, run, stats
px,f=load(); pd.set_option("display.width",250)
fh=pd.Series(0.0,index=px.index); fh[px.index.hour.isin([0,8,16])]=0.0001
ix=f.index.intersection(fh.index); fh.loc[ix]=f.reindex(ix)
rows={}
for per,a in [("2017-2026",None),("2023-2026","2023-01-01")]:
  sub=px.loc[a:] if a else px
  Y=(sub.index[-1]-sub.index[0]).days/365.25
  for dip,hold in [(0.05,24),(0.05,48),(0.05,72),(0.07,24),(0.07,36)]:
    for lev in [1,2,3,5,10]:
      t=run(sub,dip=dip,lookback=24,hold=hold,lev=lev,funding=fh,cost=0.0005 if lev>1 else 0.001)
      s=stats(t,Y)
      rows[(per,f"{int(dip*100)}%/{hold}h",lev)]=dict(trades=s["trades"],avg=s["avg"]*100,total=s["total"]*100,cagr=s["cagr"]*100,maxdd=s["maxdd"]*100,worst=s["worst"]*100,liq=s["liq"])
d=pd.DataFrame(rows).T
print(d.round(1).to_string())
d.to_csv("data/leverage.csv")
