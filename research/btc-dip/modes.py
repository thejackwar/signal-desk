import numpy as np, pandas as pd
from backtest import load, run
px,_=load(); pd.set_option("display.width",250)
rows={}
for mode in ["limit","close"]:
  for dip in [0.05,0.07]:
    for hold in [4,12,24,36,48,72,96]:
      t=run(px,dip=dip,lookback=24,hold=hold,entry_mode=mode)
      o=t[t.entry_time>="2023"]
      rows[(mode,dip,hold)]=dict(all_avg=t.ret.mean()*100,all_win=(t.ret>0).mean()*100,n=len(t),o_avg=o.ret.mean()*100,o_win=(o.ret>0).mean()*100,o_n=len(o))
print(pd.DataFrame(rows).T.round(2))
