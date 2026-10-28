// helper for test_rating.py: reads {cases:[{candles}]} on stdin, prints indicator values and ratings
import * as R from "../../docs/rating.js";
const job = JSON.parse(await new Promise(ok => { let s = ""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => ok(s)); }));
const out = job.cases.map(({ candles }) => {
  const c = candles.map(x => x.close), h = candles.map(x => x.high), l = candles.map(x => x.low);
  const L = v => v[v.length - 1];
  const st = R.stoch(h, l, c), ax = R.adx(h, l, c), m = R.macd(c);
  return {
    sma50: L(R.sma(c, 50)), ema50: L(R.ema(c, 50)), ema200: L(R.ema(c, 200)), rsi: L(R.rsi(c)), stochK: L(st.k), stochD: L(st.d),
    cci: L(R.cci(h, l, c)), adx: L(ax.adx), pdi: L(ax.pdi), ndi: L(ax.ndi), macd: L(m.line), signal: L(m.signal),
    wr: L(R.williams(h, l, c)), rating: R.rate(candles),
  };
});
process.stdout.write(JSON.stringify(out));
