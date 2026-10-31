import numpy as np, pandas as pd
from backtest import load, run
px,_=load(); c=px.close
pd.set_option("display.width",250)
out={}
for hold in [1,2,4,6,8,12,24,36,48,72,96,120]:
    fwd=(c.shift(-hold)/c-1)
    row={"base_all":fwd.mean()*100-0.2,"base_2023+":fwd["2023":].mean()*100-0.2}
    for dip in [0.05,0.07,0.10]:
        t=run(px,dip=dip,lookback=24,hold=hold)
        r=t.ret*100; o=t[t.entry_time>="2023"].ret*100
        row[f"{int(dip*100)}%_all"]=r.mean(); row[f"{int(dip*100)}%_t"]=r.mean()/(r.std()/np.sqrt(len(r)))
        row[f"{int(dip*100)}%_2023+"]=o.mean(); row[f"{int(dip*100)}%_t23"]=o.mean()/(o.std()/np.sqrt(len(o)))
    out[hold]=row
print(pd.DataFrame(out).T.round(2))
# by year, 5% / 24h lb, several holds
for dip in [0.05,0.07]:
  print(f"\n== {int(dip*100)}% dip by year: avg trade % (n) ==")
  yr={}
  for hold in [4,12,24,48,96]:
    t=run(px,dip=dip,lookback=24,hold=hold); t["y"]=t.entry_time.dt.year
    yr[f"{hold}h"]=t.groupby("y").ret.apply(lambda s:f"{s.mean()*100:+.2f} ({len(s)})")
  print(pd.DataFrame(yr))
