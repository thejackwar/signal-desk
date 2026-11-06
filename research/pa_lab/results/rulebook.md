# Price Action Lab rulebook

Every price action rule as dials with fixed values. Markets: QQQ (main, in place of NQ) and SPY (check, in place of ES),
1-minute bars, regular hours 09:30-16:00 New York. Nothing here changes after a result; changes go in numbered amendments in decisions.md.

## Detectors

- **Swing** of strength k: a high (low) above (below) the k bars on each side; known only k bars later.
- **Trend**: up when the last two swing highs and lows both rise, down when both fall.
- **Break of structure / change of character**: the first close past the last confirmed swing; with the trend it is a break of structure, against it a change of character.
- **Level**: a cluster of swings within 0.1, 0.25 or 0.5 ATR, needing 1-4 touches, drawn as a line or a box.
- **Trend line**: through the last two rising swing lows (or falling swing highs).
- **Supply/demand zone**: the base candle before a run of 2-5 same-colour candles covering 1.5-3 ATR.
- **Fair value gap**: three candles where the third's low is above the first's high (or the mirror).
- **ATR**: Wilder's 14-bar ATR from bars before the current one. A higher-chart bar is usable only from its close.

## Shared dials

- `direction` = long, short, both: which side the version trades
- `swing` = 2, 3, 5, 8: swing strength: bars on each side that a swing high/low must beat
- `trigger` = same, down1, down2: trigger chart: the setup chart or one or two steps lower (daily: 1h)
- `confirm` = none, close_back, rejection, engulfing, either, lower_bos: confirmation candle on the trigger chart before entering
- `entry` = close, limit: enter at the trigger bar's close, or a limit back at the level
- `stop_type` = hard, close: hard stop (intrabar) or close-based stop (trigger-chart close beyond it)
- `stop_ref` = level, wick, swing, gap_mid: what the stop sits beyond: the level, the touch wick, the last swing, or the gap's middle candle
- `stop_buf` = 0.0, 0.1, 0.25: extra distance beyond the stop reference, in setup-chart ATR
- `target` = 1R, 1.5R, 2R, 3R, 4R, 5R, swing: profit target in multiples of risk, or the next swing
- `manage` = hold, choch_exit: hold to stop/target, or exit on a change of character against the trade
- `time_exit` = none, flat_1600, 24, 96: time exit: none, flat at 16:00, or after 24 or 96 setup-chart bars

## Filters

- `f_trend` = off, setup, up1, daily: trend must agree: on the setup chart, one chart up, or the daily
- `f_choppy` = off, on: skip days after a choppy session (body under 25% of the range)
- `f_run` = off, 3, 5: skip trades in the direction of a 3 or 5 day run
- `f_fresh` = off, on: breaks only: the level was never broken before
- `f_news` = trade, skip, half: CPI, jobs and FOMC days: trade, skip, or half size
- `f_session` = all, open, afternoon: time of day: all, New York open 09:30-11:00, afternoon 13:30-16:00
- `f_pullback` = off, 0.382, 0.5: minimum pullback of the last swing leg before entering
- `f_gap` = off, on: an unfilled fair value gap must overlap the level

## Levels (bounce, breakout, flip zone)

- `lvl_tol` = 0.1, 0.25, 0.5: swings within this many ATR form one level
- `lvl_touches` = 1, 2, 3, 4: minimum touches (swings) in the level
- `lvl_shape` = line, box: level drawn as a line (average) or a box (lowest to highest swing)

## Each setup's own dials

- **Level bounce**: none beyond the shared and level dials
- **Breakout**
  - `bo_margin` = 0.0, 0.1: close must clear the level by this many ATR
- **Flip zone**
  - `flip_window` = 5, 10, 20: bars after the break in which the retest must come
- **Trend pullback**
  - `pb_level` = 0.382, 0.5, 0.618: retracement of the last leg that triggers the setup
- **Trend line**
  - `tl_tol` = 0.1, 0.25: how close (ATR) a touch must come to the line
- **Break of structure**
  - `retest` = no, yes: enter on the break, or wait up to 10 bars for a retest of the broken swing
- **Change of character**
  - `retest` = no, yes: enter on the change of character, or wait for a retest
- **Sweep and reclaim**
  - `sweep_depth` = 0.05, 0.1, 0.25: how far past the swing (ATR) the sweep must reach
  - `reclaim_bars` = 1, 2, 3: bars allowed to close back inside
- **Supply and demand zone**
  - `zone_first` = yes, no: only the first return to a zone, or any return
  - `zone_age` = 50, 200: zones older than this many bars are dropped
- **Fair value gap**
  - `gap_min` = 0.0, 0.1, 0.25: minimum gap size in ATR
  - `gap_first` = yes, no: only the first return into the gap, or any

## Removed combinations

- a trigger chart that does not exist (1m has nothing lower; daily triggers on 1h only)
- the gap's middle candle as a stop outside the fair value gap setup
- flat at 16:00 on a 4h or daily setup
- a lower-chart break of structure as confirmation when the trigger is the setup chart
- 'fresh breakouts only' outside the break setups; 'one chart up' trend and time-of-day filters on a daily setup
- gap confluence on the gap setup itself; a touch-wick stop on a break entered without a retest

## Valid versions

- Level bounce: 48,063,891,456
- Breakout: 192,255,565,824
- Flip zone: 288,383,348,736
- Trend pullback: 6,007,986,432
- Trend line: 4,005,324,288
- Break of structure: 6,675,540,480
- Change of character: 6,675,540,480
- Sweep and reclaim: 18,023,959,296
- Supply and demand zone: 8,010,648,576
- Fair value gap: 8,010,648,576
- **Total: 586,112,454,144**
