# Price Action Lab

Turns price action trading rules into exact rules a computer can test, on QQQ and SPY 1-minute bars
(standing in for NQ and ES futures), and judges them on two sealed years opened once. Results go to the
site's Price Action Lab page (`docs/palab.html`, data in `docs/data/palab/`).

```
config.py            every fixed number (windows, costs, thresholds, master seed)
guard.py             the seal: refuses any read of data/sealed/ or final-window sessions unless unlocked (logged)
data.py              Prompt 1: Alpaca download, audit, complete sessions, write the sealed window once (sha256)
charts.py            5m/15m/1h/4h/daily bars, Wilder ATR, swings, trend, break of structure / change of character,
                     levels, fair value gaps, supply/demand zones
dials.py             the rulebook: every rule as dials with fixed values, the validity rules, the version count
setups.py            the ten setups -> raw signals
engine.py            Prompt 2: confirmation, filters, 1-minute fills with costs, matched random twins
behavior.py          Prompt 2: behaviour tests (levels vs random lines, sweeps, gaps, zones ...) with bootstrap verdicts
versions.py          Prompt 3: spread set, common set, people sample, six named versions (saved and hashed first)
funnel.py            Prompt 3: the five-stage funnel, each setup as a whole, stage/dial/rule-effect tables
final.py             Prompt 4: hash checks, rehearsal, the one unlock, what held up
export.py            Prompt 5: findings.json for the page; provenance_check.py re-reads every number independently
news_days.csv        CPI, jobs report and FOMC days from the BLS and Federal Reserve
decisions.md         every decision and its reason; amendments are numbered and written before looking
tests/test_lab.py    the seal, no look-ahead on data cut at random bars, and the fill rules
```

## Running it

On GitHub: Actions → **Price Action Lab** → Run workflow → `discovery` (download, tests, behaviour tests, the
funnel across ten parallel jobs, publish). When that is done and you're happy nothing needs changing, run it
once more with `final`. That opens the seal; it can only happen once.

Offline, on synthetic data:

```
export PYTHONPATH=. PA_LAB_DEMO=1 PA_LAB_SCALE=0.005 PA_LAB_DATA=/tmp/palab
python -m research.pa_lab.data && python -m research.pa_lab.dials && python -m pytest research/pa_lab/tests -q
python -m research.pa_lab.behavior && python -m research.pa_lab.funnel plan && python -m research.pa_lab.funnel run
python -m research.pa_lab.funnel merge && python -m research.pa_lab.export
```

Demo results go to `$PA_LAB_DATA/demo_results`, never to `results/`.

## Rules the lab keeps

- One master seed (20261106) for every random draw.
- The final window (2024-08-01 on) is written once by the ingest and read only through the guard, with the
  `final_test` step and `PA_LAB_UNLOCK=final_test`; every unlock and relock is in `results/access_log.jsonl`.
- No threshold moves after a result; changes go in numbered amendments in `decisions.md`, written before looking.
- No look-ahead: a higher-chart bar is usable only from its close; tests rebuild every detector and setup on
  data cut at random bars and require the same output up to the cut.
- Every number on the page comes from a saved file and is re-read by a second reader before the page data is written.
