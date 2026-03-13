#!/usr/bin/env python3
"""
Analyze long gaps between trades in the swing trend strategy.
For each gap > MIN_GAP_HOURS, identify what blocked earlier entries
and rank the dominant blocker types across all cases.
"""

import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
from strategies.macd_rsi_advanced import compute_adx, compute_atr, compute_ema
from strategies.intraday_indicators import compute_hma, compute_supertrend, compute_keltner

# ── Config (must match swing_trend_dev_v1.yaml) ─────────────────────────────
HMA_PERIOD             = 21
ST_ATR_PERIOD          = 14
ST_MULT                = 3.0
KC_EMA_PERIOD          = 20
KC_ATR_PERIOD          = 14
KC_ATR_MULT            = 2.0
ADX_PERIOD             = 14
ADX_THRESHOLD          = 20
SHORT_ADX_THRESHOLD    = 25
MAX_ST_STOP_PCT        = 0.03
STOP_LOSS_PCT          = 0.03
COOLDOWN_BARS          = 3
MIN_HOLD_BARS          = 6
ENTRY_MODE             = "both"
ENABLE_SHORT           = True
WARMUP_BARS            = 30
MIN_GAP_HOURS          = 48   # only report gaps longer than this
MIN_PRICE_MOVE_PCT     = 3.0  # only report gaps where price moved more than this

# ── Load & Resample ──────────────────────────────────────────────────────────
print("Loading 5m data ...")
df = pd.read_csv('data/BTCUSDT-5m-2022-2024-combined.csv')
ts = df['open_time'].astype(float)
ts = ts.where(ts < 1e15, ts / 1000)
df['date'] = pd.to_datetime(ts, unit='ms', utc=True).dt.tz_localize(None)
df = df.set_index('date').sort_index()

col_map = {'open_price': 'open', 'high_price': 'high',
           'low_price': 'low', 'close_price': 'close'}
df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
for col in ['open', 'high', 'low', 'close', 'volume']:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
df = df[['open', 'high', 'low', 'close', 'volume']].dropna()

print("Resampling to 1h ...")
h = df.resample('1h').agg({'open':'first','high':'max','low':'min',
                            'close':'last','volume':'sum'}).dropna()
print(f"  {len(h)} hourly bars: {h.index[0]} → {h.index[-1]}")

# ── Indicators ───────────────────────────────────────────────────────────────
print("Computing indicators ...")
closes = h['close']
highs  = h['high']
lows   = h['low']

hma       = compute_hma(closes, HMA_PERIOD)
hma_slope = hma.diff()
st_line, st_bull = compute_supertrend(highs, lows, closes, ST_ATR_PERIOD, ST_MULT)
kc_upper, kc_mid, kc_lower = compute_keltner(
    highs, lows, closes, KC_EMA_PERIOD, KC_ATR_PERIOD, KC_ATR_MULT)
adx_vals  = compute_adx(highs, lows, closes, ADX_PERIOD)

ind = pd.DataFrame({
    'close'    : closes,
    'high'     : highs,
    'low'      : lows,
    'hma_slope': hma_slope,
    'st_line'  : st_line,
    'st_bull'  : st_bull.astype(bool),
    'kc_upper' : kc_upper,
    'kc_mid'   : kc_mid,
    'kc_lower' : kc_lower,
    'adx'      : adx_vals,
}, index=h.index)

# ── Per-bar entry blocker logic ───────────────────────────────────────────────
def primary_blocker(row, price_override=None):
    """
    Return (would_enter: bool, direction: str|None, blocker: str).
    Does NOT account for position state or cooldown — pure signal logic.
    """
    price  = price_override if price_override is not None else row['close']
    hslope = row['hma_slope']
    stb    = bool(row['st_bull'])
    adx_v  = row['adx']
    kcu    = row['kc_upper']
    kcm    = row['kc_mid']
    kcl    = row['kc_lower']
    stl    = row['st_line']

    if pd.isna(hslope) or pd.isna(adx_v) or pd.isna(stl):
        return False, None, 'warmup'

    # ── Determine desired direction ──
    if hslope > 0 and stb:
        direction = 'LONG'
    elif hslope < 0 and not stb:
        direction = 'SHORT'
    elif hslope > 0 and not stb:
        return False, None, 'hma_bull_st_bear'
    elif hslope < 0 and stb:
        return False, None, 'hma_bear_st_bull'
    else:
        return False, None, 'flat_hma'

    # ── ADX filter ──
    threshold = SHORT_ADX_THRESHOLD if direction == 'SHORT' else ADX_THRESHOLD
    if adx_v < threshold:
        return False, direction, 'adx_below_threshold'

    # ── Keltner trigger ──
    trigger = None
    if direction == 'LONG':
        if ENTRY_MODE in ('breakout', 'both') and price > kcu:
            trigger = 'breakout'
        elif ENTRY_MODE in ('midline', 'both'):
            if row['low'] <= kcm * 1.002 and price > kcm:
                trigger = 'pullback'
    else:  # SHORT
        if ENTRY_MODE in ('breakout', 'both') and price < kcl:
            trigger = 'breakout'
        elif ENTRY_MODE in ('midline', 'both'):
            if row['high'] >= kcm * 0.998 and price < kcm:
                trigger = 'pullback'

    if trigger is None:
        return False, direction, 'no_kc_trigger'

    # ── Stop distance ──
    if direction == 'LONG':
        stop_dist = (price - stl) / price
    else:
        stop_dist = (stl - price) / price

    if stop_dist > MAX_ST_STOP_PCT:
        return False, direction, 'st_stop_too_wide'
    if stop_dist < 0:
        return False, direction, 'st_wrong_side'

    return True, direction, trigger


# ── Full strategy simulation (state machine) ─────────────────────────────────
print("Simulating strategy ...")

in_position       = False
position_dir      = None
entry_bar_i       = None
entry_price       = None
hard_stop         = None
peak_price        = None
last_exit_bar_i   = -999

trade_log = []   # list of dicts with entry/exit bar indices

n = len(ind)
ind_arr = ind.values        # faster row access
ind_cols = list(ind.columns)
ci = {c: i for i, c in enumerate(ind_cols)}

for i in range(n):
    row   = ind.iloc[i]
    price = row['close']
    bar_h = row['high']
    bar_l = row['low']

    if in_position:
        bars_held = i - entry_bar_i
        in_min_hold = bars_held < MIN_HOLD_BARS

        stl   = row['st_line']
        stb   = bool(row['st_bull'])
        hslp  = row['hma_slope']

        # Breakeven adjustment
        if position_dir == 'LONG':
            unr = (price - entry_price) / entry_price
            if unr >= 0.015:
                hard_stop = max(hard_stop, entry_price)
        else:
            unr = (entry_price - price) / entry_price
            if unr >= 0.015:
                hard_stop = min(hard_stop, entry_price)

        exit_reason = None

        if position_dir == 'LONG':
            trail_val  = stl if not pd.isna(stl) else hard_stop
            active_stop = max(hard_stop, trail_val) if not in_min_hold else hard_stop
            peak_price  = max(peak_price, price)
            if bar_l <= active_stop:
                exit_reason = 'hard_stop' if active_stop == hard_stop else 'st_trailing'
            elif not in_min_hold and not stb:
                exit_reason = 'st_flip'
        else:  # SHORT
            trail_val  = stl if not pd.isna(stl) else hard_stop
            active_stop = min(hard_stop, trail_val) if not in_min_hold else hard_stop
            peak_price  = min(peak_price, price)
            if bar_h >= active_stop:
                exit_reason = 'hard_stop' if active_stop == hard_stop else 'st_trailing'
            elif not in_min_hold and stb:
                exit_reason = 'st_flip'

        if exit_reason:
            trade_log.append({
                'entry_i'     : entry_bar_i,
                'exit_i'      : i,
                'direction'   : position_dir,
                'entry_ts'    : ind.index[entry_bar_i],
                'exit_ts'     : ind.index[i],
                'entry_price' : entry_price,
                'exit_price'  : price,
                'exit_reason' : exit_reason,
                'bars_held'   : bars_held,
            })
            in_position     = False
            position_dir    = None
            last_exit_bar_i = i
        continue   # skip entry check while in position

    # ── Entry check ──
    if i < WARMUP_BARS:
        continue
    if (i - last_exit_bar_i) < COOLDOWN_BARS:
        continue

    can_enter, direction, reason = primary_blocker(row)
    if can_enter and direction is not None:
        in_position   = True
        position_dir  = direction
        entry_bar_i   = i
        entry_price   = price
        peak_price    = price
        if direction == 'LONG':
            hard_stop = price * (1 - STOP_LOSS_PCT)
        else:
            hard_stop = price * (1 + STOP_LOSS_PCT)

print(f"  Simulated {len(trade_log)} trades")

# ── Gap analysis ─────────────────────────────────────────────────────────────
print("\nAnalyzing gaps ...")

gaps = []

# Include a synthetic "first gap" from warmup end to first trade
first_entry_i = trade_log[0]['entry_i'] if trade_log else n
if first_entry_i > WARMUP_BARS + 24:
    gap_start_i = WARMUP_BARS
    gap_end_i   = first_entry_i
    gaps.append((gap_start_i, gap_end_i, "START"))

# Gaps between consecutive trades
for j in range(len(trade_log) - 1):
    exit_i      = trade_log[j]['exit_i']
    next_entry_i = trade_log[j+1]['entry_i']
    gap_h       = next_entry_i - exit_i
    if gap_h >= MIN_GAP_HOURS:
        gaps.append((exit_i, next_entry_i, j))

print(f"  Gaps >= {MIN_GAP_HOURS}h: {len(gaps)}")

# ── Diagnose each gap ─────────────────────────────────────────────────────────
def describe_gap(gap_start_i, gap_end_i, trade_idx):
    """Tally per-bar blockers across the gap, return a dict summary."""
    # Skip first COOLDOWN_BARS after exit
    analysis_start = gap_start_i + COOLDOWN_BARS + 1
    analysis_end   = gap_end_i

    if analysis_start >= analysis_end:
        return None

    blocker_counts = {}
    for i in range(analysis_start, analysis_end):
        row = ind.iloc[i]
        _, direction, blocker = primary_blocker(row)
        # For counting purposes, we track the FIRST blocker in the chain
        # (ADX blocks before Keltner, so ADX is the root cause)
        blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1

    total_bars   = analysis_end - analysis_start
    gap_start_ts = ind.index[gap_start_i]
    gap_end_ts   = ind.index[gap_end_i]
    start_price  = ind.iloc[gap_start_i]['close']
    end_price    = ind.iloc[gap_end_i]['close']
    price_move   = (end_price - start_price) / start_price * 100

    # Sort by count
    ranked = sorted(blocker_counts.items(), key=lambda x: -x[1])

    return {
        'trade_idx'   : trade_idx,
        'gap_start_ts': gap_start_ts,
        'gap_end_ts'  : gap_end_ts,
        'gap_hours'   : gap_end_i - gap_start_i,
        'gap_days'    : (gap_end_i - gap_start_i) / 24,
        'start_price' : start_price,
        'end_price'   : end_price,
        'price_move_pct': price_move,
        'total_bars'  : total_bars,
        'blockers'    : dict(ranked),
        'dominant'    : ranked[0][0] if ranked else 'unknown',
        'dominant_pct': ranked[0][1] / total_bars * 100 if ranked else 0,
    }

gap_summaries = []
for gap_start_i, gap_end_i, trade_idx in gaps:
    g = describe_gap(gap_start_i, gap_end_i, trade_idx)
    if g and abs(g['price_move_pct']) >= MIN_PRICE_MOVE_PCT:
        gap_summaries.append(g)

gap_summaries.sort(key=lambda x: -x['gap_days'])

# ── Report ────────────────────────────────────────────────────────────────────
print(f"\n{'═'*70}")
print(f"ENTRY GAP ANALYSIS — {len(gap_summaries)} gaps ≥ {MIN_GAP_HOURS}h with |price move| ≥ {MIN_PRICE_MOVE_PCT}%")
print(f"{'═'*70}")

print(f"\n{'─'*70}")
print("TOP 15 LONGEST GAPS (sorted by duration)")
print(f"{'─'*70}")

for g in gap_summaries[:15]:
    direction_arrow = "↑" if g['price_move_pct'] > 0 else "↓"
    print(f"\n  {g['gap_start_ts'].strftime('%Y-%m-%d %H:%M')} → "
          f"{g['gap_end_ts'].strftime('%Y-%m-%d %H:%M')}  "
          f"({g['gap_days']:.1f}d, {g['gap_hours']}h)")
    print(f"  Price: {g['start_price']:,.0f} → {g['end_price']:,.0f}  "
          f"{direction_arrow} {g['price_move_pct']:+.1f}%")
    print(f"  Dominant blocker: {g['dominant']} ({g['dominant_pct']:.0f}% of bars)")
    # Show top 3 blockers
    b_items = list(g['blockers'].items())[:4]
    b_str = ', '.join(f"{k}: {v}h ({v/g['total_bars']*100:.0f}%)"
                      for k, v in b_items)
    print(f"  All blockers: {b_str}")

# ── Global blocker distribution ───────────────────────────────────────────────
print(f"\n{'─'*70}")
print("GLOBAL DOMINANT-BLOCKER DISTRIBUTION (across all qualifying gaps)")
print(f"{'─'*70}")
dominant_counter = {}
weighted_counter = {}  # weighted by gap duration
for g in gap_summaries:
    d = g['dominant']
    dominant_counter[d] = dominant_counter.get(d, 0) + 1
    weighted_counter[d] = weighted_counter.get(d, 0) + g['gap_hours']

print("\n  By count of gaps:")
for k, v in sorted(dominant_counter.items(), key=lambda x: -x[1]):
    pct = v / len(gap_summaries) * 100
    print(f"    {k:<30}  {v:3d} gaps  ({pct:.0f}%)")

print("\n  By total hours blocked:")
total_h = sum(weighted_counter.values())
for k, v in sorted(weighted_counter.items(), key=lambda x: -x[1]):
    pct = v / total_h * 100
    print(f"    {k:<30}  {v:5d}h  ({pct:.0f}%)")

# ── Bar-level blocker across ALL non-position bars ────────────────────────────
print(f"\n{'─'*70}")
print("BAR-LEVEL BLOCKER DISTRIBUTION (all out-of-position bars after warmup)")
print(f"{'─'*70}")
all_blocker_counts = {}
for i in range(WARMUP_BARS, n):
    # Only count bars when we're not in position
    # (approximate: use the trade log to identify in-position windows)
    row = ind.iloc[i]
    _, _, b = primary_blocker(row)
    all_blocker_counts[b] = all_blocker_counts.get(b, 0) + 1

total_out = sum(all_blocker_counts.values())
print(f"\n  Total out-of-position bars: {total_out}")
for k, v in sorted(all_blocker_counts.items(), key=lambda x: -x[1]):
    pct = v / total_out * 100
    bar_str = f"{v:5d} bars"
    print(f"    {k:<30}  {bar_str}  ({pct:.1f}%)")

# ── Price-weighted blocker impact (how much of missed move is each blocker?) ──
print(f"\n{'─'*70}")
print("PRICE MOVE WEIGHTED BY DOMINANT BLOCKER (|move| × hours)")
print(f"{'─'*70}")
price_impact = {}
for g in gap_summaries:
    d = g['dominant']
    impact = abs(g['price_move_pct']) * g['gap_hours']
    price_impact[d] = price_impact.get(d, 0) + impact

total_impact = sum(price_impact.values())
for k, v in sorted(price_impact.items(), key=lambda x: -x[1]):
    pct = v / total_impact * 100
    print(f"    {k:<30}  score={v:7.0f}  ({pct:.0f}%)")

print(f"\n{'═'*70}")
print("Done.")
