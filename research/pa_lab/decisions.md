# Price Action Lab: decisions

Every decision and its reason, written before results are seen. Changes after a result go in numbered
amendments (A-01, A-02 …) written before looking at what they affect. Master seed: 20261106.

## Setup (2026-11-06, before any real data was downloaded)

- **D-01 Markets.** The spec uses NQ and ES 1-minute futures. There is no free source for those, so QQQ plays
  NQ's role (rules are found on it) and SPY plays ES's role (the check market). Alpaca 1-minute SIP bars,
  `adjustment=all` (split and dividend adjusted) stand in for back-adjusted futures; the unadjusted bars are
  kept for cross-checks. Dividend days stand in for rolls in the audit (adjusted minus unadjusted steps only there).
- **D-02 Sessions.** ETFs have no overnight session worth trading, so a session is 09:30-16:00 New York, dated by
  its own day. "Flat at 16:00" exits at the close of the 15:59 bar. There are no rolls, so the
  no-entries-before-a-roll rule has nothing to act on. Positions may still be held overnight.
- **D-03 Session filter.** "All", "London and New York" and "New York 08:00-16:00" are identical in regular hours;
  the dial keeps "all" and "New York open 09:30-11:00" and adds "afternoon 13:30-16:00".
- **D-04 Charts.** 5m, 15m, 1h bars are counted from 09:30 (the last 1h bar is 15:30-16:00); the 4h bars are
  09:30-13:30 and 13:30-16:00; the daily bar is the session.
- **D-05 Costs.** One futures contract becomes 100 shares: $1 commission per round trip and 1 tick ($0.01) of
  slippage on market entries, hard stops, close-based stops and structure exits. Half size on news = 50 shares.
- **D-06 Levels.** Levels (swing clusters) are rebuilt when a new swing is confirmed, with that bar's ATR, not
  every bar; it keeps 1-minute charts tractable and uses no future data. The last 40 swings are remembered.
- **D-07 Supply and demand zones** are known at the first bar where the run of 2-5 same-colour candles covers
  1.5-3 ATR (recording them at the end of the run would peek at whether the run continues).
- **D-08 Confirmation** must come within 12 trigger-chart bars of the setup; a limit back at the level stays
  live for 12 trigger bars (and never past 16:00 for flat-at-16:00 versions).
- **D-09 "Next swing" target** is the last confirmed opposite swing at the signal; if it is not beyond the entry
  the trade is skipped.
- **D-10 Complete sessions.** A session is kept when it opens by 09:35 and has a bar opening at 15:59 or later,
  or is a known 13:00 early close. The same rule applies in discovery and in the final window.
- **D-11 Matched random twins** are drawn on random discovery sessions (random minute in the same 30-minute slot
  of the day); stop and target distances are in the setup chart's ATR at the twin's entry.
- **D-12 Behaviour tests** pool the 5m, 15m and 1h charts at swing strength 3. Touches and random lines are
  compared from the same starting geometry (the touch bar closes back within 0.5 ATR of the line), and each
  random line or zone is matched in distance to its real one; checked on synthetic random-walk data, where every
  placebo comparison sits near zero.
- **D-13 Final P&L files.** Every version's final metrics are committed; the full daily P&L matrices are too large
  for the repository and are attached to a GitHub release (`pa-lab-final`) instead, since they cannot be rebuilt
  without opening the seal again.
- **D-14 Windows** as in the spec: warm-up 2021-10-01 to 2021-12-31, discovery 2022-01-03 to 2024-07-31
  (era 1 = 2022, era 2 = 2023 to July 2024), final window from 2024-08-01 to the last full session at ingest.
- **D-15 Version budget.** Spread set 4,000 per setup, people sample 4,800 per setup, common grid (14,536 versions,
  close to the spec's 14,016), six named versions. Valid versions of the dial book: 586,112,454,144.
