#!/usr/bin/env python3
"""
Analyze hypothetical entries blocked ONLY by ADX < threshold.

For every hourly bar where:
  - HMA + ST agree on a direction
  - At least one KC trigger fires (breakout, pullback, or midline hold N=1)
  - ST stop distance is OK
  - But ADX < threshold

...simulate the trade using the real exit logic (hard stop, ST trailing, ST flip,
breakeven, min_hold_bars). Then classify as winner/loser and correlate with a
rich set of indicator features to find which ones discriminate winners from losers.

Usage:
    python3 analyze_adx_blocked_entries.py [dev|test]
"""

import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
from scipy import stats
from strategies.macd_rsi_advanced import compute_adx, compute_atr, compute_ema, compute_rsi
from strategies.intraday_indicators import compute_hma, compute_supertrend, compute_keltner

# ── Which dataset? ─────────────────────────────────────────────────────────────
dataset = sys.argv[1] if len(sys.argv) > 1 else "dev"
if dataset == "dev":
    DATA_FILE = "data/BTCUSDT-5m-2022-2024-combined.csv"
    LABEL = "DEV (2022-2024)"
elif dataset == "test":
    DATA_FILE = "data/BTCUSDT-5m-test-combined.csv"
    LABEL = "TEST (2020-2021, 2025, Jan2026)"
else:
    print(f"Unknown dataset '{dataset}', use 'dev' or 'test'")
    sys.exit(1)

# ── Strategy config (v2 — matches swing_trend_dev_v2.yaml) ────────────────────
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
BREAKEVEN_TRIGGER_PCT  = 0.015
COOLDOWN_BARS          = 3
MIN_HOLD_BARS          = 6
ENTRY_MODE             = "both"
ENABLE_SHORT           = True
KC_MIDLINE_HOLD_BARS   = 1   # v2 trigger
WARMUP_BARS            = 30

# ── Load & Resample ──────────────────────────────────────────────────────────
print(f"Loading 5m data ({LABEL}) ...")
df = pd.read_csv(DATA_FILE)
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
h = df.resample('1h').agg({'open':'first', 'high':'max', 'low':'min',
                            'close':'last', 'volume':'sum'}).dropna()
print(f"  {len(h)} hourly bars: {h.index[0]} → {h.index[-1]}")

# ── Indicators ───────────────────────────────────────────────────────────────
print("Computing indicators ...")
closes = h['close']
highs  = h['high']
lows   = h['low']
opens  = h['open']
volumes = h['volume']

# Standard indicators (same as strategy)
hma       = compute_hma(closes, HMA_PERIOD)
hma_slope = hma.diff()
st_line, st_bull = compute_supertrend(highs, lows, closes, ST_ATR_PERIOD, ST_MULT)
kc_upper, kc_mid, kc_lower = compute_keltner(
    highs, lows, closes, KC_EMA_PERIOD, KC_ATR_PERIOD, KC_ATR_MULT)
adx_14    = compute_adx(highs, lows, closes, ADX_PERIOD)
atr_14    = compute_atr(highs, lows, closes, 14)
rsi_14    = compute_rsi(closes, 14)

# Additional indicators for correlation analysis
adx_7     = compute_adx(highs, lows, closes, 7)
adx_5     = compute_adx(highs, lows, closes, 5)
adx_10    = compute_adx(highs, lows, closes, 10)
atr_7     = compute_atr(highs, lows, closes, 7)

# +DI and -DI (recompute from raw DM)
prev_high = highs.shift(1)
prev_low  = lows.shift(1)
prev_close = closes.shift(1)
tr = pd.concat([highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()], axis=1).max(axis=1)
plus_dm  = (highs - prev_high).where((highs - prev_high) > (prev_low - lows), 0.0).clip(lower=0)
minus_dm = (prev_low - lows).where((prev_low - lows) > (highs - prev_high), 0.0).clip(lower=0)
atr_smooth = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
plus_di_14  = 100 * (plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_smooth)
minus_di_14 = 100 * (minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_smooth)

# Volume SMA for relative volume
vol_sma_20 = volumes.rolling(20).mean()

print("  Done.")

# ── Build unified DataFrame ──────────────────────────────────────────────────
ind = pd.DataFrame({
    'open': opens, 'high': highs, 'low': lows, 'close': closes, 'volume': volumes,
    'hma': hma, 'hma_slope': hma_slope,
    'st_line': st_line, 'st_bull': st_bull.astype(bool),
    'kc_upper': kc_upper, 'kc_mid': kc_mid, 'kc_lower': kc_lower,
    'adx_14': adx_14, 'adx_7': adx_7, 'adx_5': adx_5, 'adx_10': adx_10,
    'atr_14': atr_14, 'atr_7': atr_7,
    'rsi_14': rsi_14,
    'plus_di_14': plus_di_14, 'minus_di_14': minus_di_14,
    'vol_sma_20': vol_sma_20,
}, index=h.index)

n_bars = len(ind)

# ── Find ADX-only-blocked entries ─────────────────────────────────────────────
print("\nFinding ADX-only-blocked entries ...")

def check_kc_trigger(i, direction, row):
    """Check if any KC trigger (breakout, pullback, midline hold N=1) fires."""
    price = row['close']
    kcu = row['kc_upper']
    kcm = row['kc_mid']
    kcl = row['kc_lower']

    if direction == 'LONG':
        # Breakout
        if ENTRY_MODE in ('breakout', 'both') and price > kcu:
            return 'keltner_breakout'
        # Pullback
        if ENTRY_MODE in ('midline', 'both'):
            if row['low'] <= kcm * 1.002 and price > kcm:
                return 'keltner_pullback'
        # Midline hold (v2): last N=1 COMPLETED bar close above midline
        if KC_MIDLINE_HOLD_BARS > 0 and i >= KC_MIDLINE_HOLD_BARS + 1:
            # Use i-1 (last completed bar) to avoid lookahead
            prev_close = ind.iloc[i - 1]['close']
            prev_kcm   = ind.iloc[i - 1]['kc_mid']
            if prev_close > prev_kcm:
                return 'kc_midline_hold'
    else:  # SHORT
        if ENTRY_MODE in ('breakout', 'both') and price < kcl:
            return 'keltner_breakout'
        if ENTRY_MODE in ('midline', 'both'):
            if row['high'] >= kcm * 0.998 and price < kcm:
                return 'keltner_pullback'
        if KC_MIDLINE_HOLD_BARS > 0 and i >= KC_MIDLINE_HOLD_BARS + 1:
            prev_close = ind.iloc[i - 1]['close']
            prev_kcm   = ind.iloc[i - 1]['kc_mid']
            if prev_close < prev_kcm:
                return 'kc_midline_hold'
    return None


def check_st_stop_ok(direction, price, stl):
    """Check ST stop distance is within limits."""
    if direction == 'LONG':
        dist = (price - stl) / price if price > 0 else 0
    else:
        dist = (stl - price) / price if price > 0 else 0
    return 0 <= dist <= MAX_ST_STOP_PCT


def simulate_trade(entry_i, direction, entry_price):
    """
    Simulate trade from entry_i using standard exit logic.
    Returns dict with trade results.
    """
    hard_stop = (entry_price * (1 - STOP_LOSS_PCT) if direction == 'LONG'
                 else entry_price * (1 + STOP_LOSS_PCT))
    peak = entry_price
    trough = entry_price

    for j in range(entry_i + 1, n_bars):
        row = ind.iloc[j]
        price = row['close']
        bar_h = row['high']
        bar_l = row['low']
        stl   = row['st_line']
        stb   = bool(row['st_bull'])
        bars_held = j - entry_i
        in_min_hold = bars_held < MIN_HOLD_BARS

        # Track MFE/MAE
        if direction == 'LONG':
            peak = max(peak, price)
            trough = min(trough, price)
        else:
            peak = min(peak, price)    # for short, peak is lowest price
            trough = max(trough, price)  # worst price is highest

        # Breakeven
        if direction == 'LONG':
            unr = (price - entry_price) / entry_price
            if unr >= BREAKEVEN_TRIGGER_PCT:
                hard_stop = max(hard_stop, entry_price)
        else:
            unr = (entry_price - price) / entry_price
            if unr >= BREAKEVEN_TRIGGER_PCT:
                hard_stop = min(hard_stop, entry_price)

        exit_reason = None
        trail_val = stl if not pd.isna(stl) else hard_stop

        if direction == 'LONG':
            active_stop = max(hard_stop, trail_val) if not in_min_hold else hard_stop
            if bar_l <= active_stop:
                exit_reason = 'hard_stop' if active_stop == hard_stop else 'st_trailing'
            elif not in_min_hold and not stb:
                exit_reason = 'st_flip'
        else:
            active_stop = min(hard_stop, trail_val) if not in_min_hold else hard_stop
            if bar_h >= active_stop:
                exit_reason = 'hard_stop' if active_stop == hard_stop else 'st_trailing'
            elif not in_min_hold and stb:
                exit_reason = 'st_flip'

        if exit_reason:
            if direction == 'LONG':
                if exit_reason in ('hard_stop', 'st_trailing'):
                    exit_price = min(price, active_stop)
                else:
                    exit_price = price
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                mfe_pct = (peak - entry_price) / entry_price * 100
                mae_pct = (trough - entry_price) / entry_price * 100
            else:
                if exit_reason in ('hard_stop', 'st_trailing'):
                    exit_price = max(price, active_stop)
                else:
                    exit_price = price
                pnl_pct = (entry_price - exit_price) / entry_price * 100
                mfe_pct = (entry_price - peak) / entry_price * 100
                mae_pct = (entry_price - trough) / entry_price * 100

            return {
                'entry_i': entry_i, 'exit_i': j,
                'direction': direction,
                'entry_price': entry_price, 'exit_price': exit_price,
                'pnl_pct': pnl_pct, 'mfe_pct': mfe_pct, 'mae_pct': mae_pct,
                'bars_held': bars_held, 'exit_reason': exit_reason,
                'winner': pnl_pct > 0,
            }

    # No exit found (end of data)
    last_price = ind.iloc[-1]['close']
    if direction == 'LONG':
        pnl_pct = (last_price - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - last_price) / entry_price * 100
    return {
        'entry_i': entry_i, 'exit_i': n_bars - 1,
        'direction': direction,
        'entry_price': entry_price, 'exit_price': last_price,
        'pnl_pct': pnl_pct, 'mfe_pct': 0, 'mae_pct': 0,
        'bars_held': n_bars - 1 - entry_i, 'exit_reason': 'end_of_data',
        'winner': pnl_pct > 0,
    }


# Find all ADX-only-blocked bars and simulate trades
blocked_entries = []
last_sim_exit_i = -999  # prevent overlapping simulated trades

for i in range(WARMUP_BARS, n_bars):
    row = ind.iloc[i]
    price = row['close']
    hslope = row['hma_slope']
    stb = bool(row['st_bull'])
    adx_v = row['adx_14']
    stl = row['st_line']

    if pd.isna(hslope) or pd.isna(adx_v) or pd.isna(stl):
        continue

    # Skip if still within cooldown of last simulated trade
    if (i - last_sim_exit_i) < COOLDOWN_BARS:
        continue

    # Step 1: HMA + ST must agree
    if hslope > 0 and stb:
        direction = 'LONG'
    elif hslope < 0 and not stb and ENABLE_SHORT:
        direction = 'SHORT'
    else:
        continue  # no agreement, ADX isn't the blocker

    # Step 2: ADX must be BELOW threshold (this is what we're studying)
    threshold = SHORT_ADX_THRESHOLD if direction == 'SHORT' else ADX_THRESHOLD
    if adx_v >= threshold:
        continue  # ADX wouldn't block → skip

    # Step 3: At least one KC trigger must fire
    trigger = check_kc_trigger(i, direction, row)
    if trigger is None:
        continue  # no KC trigger → ADX isn't the only blocker

    # Step 4: ST stop distance must be OK
    if not check_st_stop_ok(direction, price, stl):
        continue

    # --- This is a genuine ADX-only-blocked entry ---
    # Simulate the trade
    trade = simulate_trade(i, direction, price)
    if trade is None:
        continue

    # Record indicator features at entry
    adx7  = row['adx_7'] if not pd.isna(row['adx_7']) else np.nan
    adx5  = row['adx_5'] if not pd.isna(row['adx_5']) else np.nan
    adx10 = row['adx_10'] if not pd.isna(row['adx_10']) else np.nan

    # ADX rate of change
    adx_delta_1 = adx_v - ind.iloc[i-1]['adx_14'] if i >= 1 else np.nan
    adx_delta_3 = adx_v - ind.iloc[i-3]['adx_14'] if i >= 3 else np.nan
    adx7_delta_1 = adx7 - ind.iloc[i-1]['adx_7'] if i >= 1 and not pd.isna(ind.iloc[i-1]['adx_7']) else np.nan
    adx7_delta_3 = adx7 - ind.iloc[i-3]['adx_7'] if i >= 3 and not pd.isna(ind.iloc[i-3]['adx_7']) else np.nan

    # HMA slope magnitude (normalized by price)
    hma_slope_norm = abs(hslope) / price * 100 if price > 0 else np.nan

    # ATR as % of price
    atr_pct = row['atr_14'] / price * 100 if price > 0 else np.nan
    atr7_pct = row['atr_7'] / price * 100 if price > 0 else np.nan

    # KC bandwidth
    kc_bw = (row['kc_upper'] - row['kc_lower']) / row['kc_mid'] * 100 if row['kc_mid'] > 0 else np.nan

    # Price position in KC channel (0 = at lower, 1 = at upper)
    kc_range = row['kc_upper'] - row['kc_lower']
    kc_position = (price - row['kc_lower']) / kc_range if kc_range > 0 else np.nan

    # Distance from HMA
    hma_dist_pct = (price - row['hma']) / price * 100 if price > 0 and not pd.isna(row['hma']) else np.nan

    # ST stop distance as %
    if direction == 'LONG':
        st_dist_pct = (price - stl) / price * 100
    else:
        st_dist_pct = (stl - price) / price * 100

    # Volume ratio
    vol_ratio = row['volume'] / row['vol_sma_20'] if not pd.isna(row['vol_sma_20']) and row['vol_sma_20'] > 0 else np.nan

    # RSI
    rsi_v = row['rsi_14'] if not pd.isna(row['rsi_14']) else np.nan

    # +DI / -DI
    pdi = row['plus_di_14'] if not pd.isna(row['plus_di_14']) else np.nan
    mdi = row['minus_di_14'] if not pd.isna(row['minus_di_14']) else np.nan
    di_spread = pdi - mdi if not (pd.isna(pdi) or pd.isna(mdi)) else np.nan
    # Directional DI spread: positive means DI aligns with trade direction
    if direction == 'LONG':
        dir_di_spread = di_spread
    else:
        dir_di_spread = -di_spread

    # Returns (price momentum)
    ret_1 = (price / ind.iloc[i-1]['close'] - 1) * 100 if i >= 1 else np.nan
    ret_3 = (price / ind.iloc[i-3]['close'] - 1) * 100 if i >= 3 else np.nan
    ret_6 = (price / ind.iloc[i-6]['close'] - 1) * 100 if i >= 6 else np.nan
    ret_12 = (price / ind.iloc[i-12]['close'] - 1) * 100 if i >= 12 else np.nan
    ret_24 = (price / ind.iloc[i-24]['close'] - 1) * 100 if i >= 24 else np.nan

    # Directional returns (positive = aligned with trade)
    sign = 1 if direction == 'LONG' else -1
    dir_ret_1 = ret_1 * sign if not pd.isna(ret_1) else np.nan
    dir_ret_3 = ret_3 * sign if not pd.isna(ret_3) else np.nan
    dir_ret_6 = ret_6 * sign if not pd.isna(ret_6) else np.nan
    dir_ret_12 = ret_12 * sign if not pd.isna(ret_12) else np.nan
    dir_ret_24 = ret_24 * sign if not pd.isna(ret_24) else np.nan

    # Candle body size
    body_pct = abs(price - row['open']) / price * 100 if price > 0 else np.nan

    # Upper/lower wick ratio
    candle_range = row['high'] - row['low']
    if candle_range > 0:
        if direction == 'LONG':
            # For longs, favorable wick = upper wick small, lower wick rejection
            lower_wick_pct = (min(row['open'], price) - row['low']) / candle_range * 100
        else:
            upper_wick_pct = (row['high'] - max(row['open'], price)) / candle_range * 100
            lower_wick_pct = upper_wick_pct  # alias for short
    else:
        lower_wick_pct = np.nan

    trade['features'] = {
        # ADX family
        'adx_14': adx_v,
        'adx_10': adx10,
        'adx_7': adx7,
        'adx_5': adx5,
        'adx14_delta_1h': adx_delta_1,
        'adx14_delta_3h': adx_delta_3,
        'adx7_delta_1h': adx7_delta_1,
        'adx7_delta_3h': adx7_delta_3,
        # DI family
        'plus_di_14': pdi,
        'minus_di_14': mdi,
        'dir_di_spread': dir_di_spread,
        # Trend strength
        'hma_slope_norm': hma_slope_norm,
        'hma_dist_pct': hma_dist_pct if direction == 'LONG' else -hma_dist_pct if not pd.isna(hma_dist_pct) else np.nan,
        # Volatility
        'atr_14_pct': atr_pct,
        'atr_7_pct': atr7_pct,
        'kc_bandwidth': kc_bw,
        # Position
        'kc_position': kc_position if direction == 'LONG' else (1 - kc_position) if not pd.isna(kc_position) else np.nan,
        'st_dist_pct': st_dist_pct,
        # Volume
        'vol_ratio': vol_ratio,
        # Momentum
        'rsi_14': rsi_v if direction == 'LONG' else (100 - rsi_v) if not pd.isna(rsi_v) else np.nan,
        'dir_ret_1h': dir_ret_1,
        'dir_ret_3h': dir_ret_3,
        'dir_ret_6h': dir_ret_6,
        'dir_ret_12h': dir_ret_12,
        'dir_ret_24h': dir_ret_24,
        # Candle
        'body_pct': body_pct,
        # Meta
        'trigger': trigger,
        'direction': direction,
    }

    blocked_entries.append(trade)
    last_sim_exit_i = trade['exit_i']

print(f"  Found {len(blocked_entries)} ADX-blocked entries")

# ── Classify winners/losers ──────────────────────────────────────────────────
winners = [t for t in blocked_entries if t['winner']]
losers  = [t for t in blocked_entries if not t['winner']]

print(f"  Winners: {len(winners)} ({len(winners)/len(blocked_entries)*100:.1f}%)")
print(f"  Losers:  {len(losers)} ({len(losers)/len(blocked_entries)*100:.1f}%)")

total_pnl = sum(t['pnl_pct'] for t in blocked_entries)
win_pnl   = sum(t['pnl_pct'] for t in winners)
lose_pnl  = sum(t['pnl_pct'] for t in losers)
avg_pnl   = total_pnl / len(blocked_entries)
avg_win   = win_pnl / len(winners) if winners else 0
avg_lose  = lose_pnl / len(losers) if losers else 0

print(f"\n  Total PnL: {total_pnl:+.1f}%")
print(f"  Avg PnL:   {avg_pnl:+.2f}%")
print(f"  Avg Win:   {avg_win:+.2f}%  |  Avg Loss: {avg_lose:+.2f}%")
print(f"  Avg MFE:   {np.mean([t['mfe_pct'] for t in blocked_entries]):.2f}%")
print(f"  Avg hold:  {np.mean([t['bars_held'] for t in blocked_entries]):.1f}h")

# ── Feature analysis ─────────────────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"FEATURE CORRELATION ANALYSIS — Winners vs Losers")
print(f"{'═'*80}")

# Collect feature names (skip 'trigger' and 'direction' which are categorical)
feature_names = [k for k in blocked_entries[0]['features'].keys()
                 if k not in ('trigger', 'direction')]

# Build feature arrays
results = []
for feat in feature_names:
    w_vals = [t['features'][feat] for t in winners if not pd.isna(t['features'][feat])]
    l_vals = [t['features'][feat] for t in losers if not pd.isna(t['features'][feat])]

    if len(w_vals) < 5 or len(l_vals) < 5:
        continue

    w_arr = np.array(w_vals)
    l_arr = np.array(l_vals)

    w_mean = np.mean(w_arr)
    l_mean = np.mean(l_arr)
    w_med  = np.median(w_arr)
    l_med  = np.median(l_arr)

    # Mann-Whitney U test (non-parametric)
    try:
        u_stat, p_value = stats.mannwhitneyu(w_arr, l_arr, alternative='two-sided')
    except ValueError:
        p_value = 1.0
        u_stat = 0

    # AUC: P(winner > loser) — how well this feature separates them
    # AUC > 0.5 means higher values → more likely winner
    # AUC < 0.5 means lower values → more likely winner
    n_w, n_l = len(w_arr), len(l_arr)
    auc = u_stat / (n_w * n_l) if n_w * n_l > 0 else 0.5

    # Effect size: difference of means / pooled std
    pooled_std = np.sqrt((np.var(w_arr) * n_w + np.var(l_arr) * n_l) / (n_w + n_l))
    cohens_d = (w_mean - l_mean) / pooled_std if pooled_std > 0 else 0

    results.append({
        'feature': feat,
        'w_mean': w_mean, 'l_mean': l_mean,
        'w_median': w_med, 'l_median': l_med,
        'n_win': n_w, 'n_lose': n_l,
        'auc': auc, 'p_value': p_value,
        'cohens_d': cohens_d,
        'abs_auc_diff': abs(auc - 0.5),
    })

# Sort by discriminative power (|AUC - 0.5|)
results.sort(key=lambda x: -x['abs_auc_diff'])

print(f"\n{'Feature':<22} {'W Mean':>8} {'L Mean':>8} {'W Med':>8} {'L Med':>8} "
      f"{'AUC':>6} {'p-val':>8} {'Cohen d':>8} {'Signal'}")
print(f"{'─'*22} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*6} {'─'*8} {'─'*8} {'─'*8}")

for r in results:
    sig = ""
    if r['p_value'] < 0.01:
        sig = "***"
    elif r['p_value'] < 0.05:
        sig = "**"
    elif r['p_value'] < 0.10:
        sig = "*"

    # Direction indicator
    if r['auc'] > 0.55:
        direction = "W↑"  # higher → winner
    elif r['auc'] < 0.45:
        direction = "W↓"  # lower → winner
    else:
        direction = "  "

    print(f"{r['feature']:<22} {r['w_mean']:>8.3f} {r['l_mean']:>8.3f} "
          f"{r['w_median']:>8.3f} {r['l_median']:>8.3f} "
          f"{r['auc']:>6.3f} {r['p_value']:>8.4f} {r['cohens_d']:>+8.3f} "
          f"{sig:>3} {direction}")

# ── Top discriminators deep dive ─────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"TOP DISCRIMINATING FEATURES (|AUC - 0.5| > 0.05)")
print(f"{'═'*80}")

top_features = [r for r in results if r['abs_auc_diff'] > 0.05]

for r in top_features:
    feat = r['feature']
    print(f"\n  ── {feat} ──")
    print(f"  Winners: mean={r['w_mean']:.4f}, median={r['w_median']:.4f} (n={r['n_win']})")
    print(f"  Losers:  mean={r['l_mean']:.4f}, median={r['l_median']:.4f} (n={r['n_lose']})")
    print(f"  AUC={r['auc']:.3f}, p={r['p_value']:.4f}, Cohen's d={r['cohens_d']:+.3f}")

    # Distribution quartiles
    w_vals = sorted([t['features'][feat] for t in winners if not pd.isna(t['features'][feat])])
    l_vals = sorted([t['features'][feat] for t in losers if not pd.isna(t['features'][feat])])

    w_q = np.percentile(w_vals, [10, 25, 50, 75, 90])
    l_q = np.percentile(l_vals, [10, 25, 50, 75, 90])

    print(f"  Winners  P10={w_q[0]:.3f}  P25={w_q[1]:.3f}  P50={w_q[2]:.3f}  P75={w_q[3]:.3f}  P90={w_q[4]:.3f}")
    print(f"  Losers   P10={l_q[0]:.3f}  P25={l_q[1]:.3f}  P50={l_q[2]:.3f}  P75={l_q[3]:.3f}  P90={l_q[4]:.3f}")

# ── Threshold analysis for top features ──────────────────────────────────────
print(f"\n{'═'*80}")
print(f"THRESHOLD ANALYSIS — Sweep thresholds on top features")
print(f"{'═'*80}")

for r in top_features[:8]:  # top 8 features
    feat = r['feature']
    all_vals = [(t['features'][feat], t['pnl_pct'], t['winner'])
                for t in blocked_entries if not pd.isna(t['features'][feat])]

    if len(all_vals) < 20:
        continue

    all_vals.sort(key=lambda x: x[0])
    vals = np.array([v[0] for v in all_vals])
    pnls = np.array([v[1] for v in all_vals])
    wins = np.array([v[2] for v in all_vals])

    # Test thresholds at 20th, 30th, 40th, 50th, 60th, 70th, 80th percentiles
    percentiles = [20, 30, 40, 50, 60, 70, 80]
    thresholds = np.percentile(vals, percentiles)

    print(f"\n  ── {feat} (AUC={r['auc']:.3f}) ──")

    # Determine if higher is better (AUC > 0.5) or lower is better
    higher_better = r['auc'] > 0.5

    print(f"  {'Direction: higher → more likely winner' if higher_better else 'Direction: lower → more likely winner'}")
    print(f"  {'Threshold':>10} {'Filter':>8} {'Trades':>7} {'WR':>6} {'AvgPnL':>8} {'SumPnL':>9} {'Kept%':>6}")

    for pctl, thresh in zip(percentiles, thresholds):
        if higher_better:
            mask = vals >= thresh
            label = f">= P{pctl}"
        else:
            mask = vals <= thresh
            label = f"<= P{pctl}"

        n_trades = mask.sum()
        if n_trades < 5:
            continue

        wr = wins[mask].mean() * 100
        avg = pnls[mask].mean()
        total = pnls[mask].sum()
        kept = n_trades / len(vals) * 100

        print(f"  {label:>10} {thresh:>8.3f} {n_trades:>7d} {wr:>5.1f}% {avg:>+7.2f}% {total:>+8.1f}% {kept:>5.1f}%")

    # Also show the complementary (filtered out) for context
    print(f"  {'ALL':>10} {'':>8} {len(vals):>7d} {wins.mean()*100:>5.1f}% "
          f"{pnls.mean():>+7.2f}% {pnls.sum():>+8.1f}% {100.0:>5.1f}%")

# ── Combined filter analysis ─────────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"COMBINED FILTER ANALYSIS — Best multi-feature filters")
print(f"{'═'*80}")

# Get top 3 statistically significant features
sig_features = [r for r in results if r['p_value'] < 0.15 and r['abs_auc_diff'] > 0.04]

if len(sig_features) >= 2:
    # Try pairwise combinations of top features
    for i_f in range(min(4, len(sig_features))):
        for j_f in range(i_f + 1, min(5, len(sig_features))):
            f1 = sig_features[i_f]
            f2 = sig_features[j_f]

            f1_higher = f1['auc'] > 0.5
            f2_higher = f2['auc'] > 0.5

            # Get median thresholds
            f1_vals = [t['features'][f1['feature']] for t in blocked_entries
                       if not pd.isna(t['features'][f1['feature']])]
            f2_vals = [t['features'][f2['feature']] for t in blocked_entries
                       if not pd.isna(t['features'][f2['feature']])]

            f1_med = np.median(f1_vals)
            f2_med = np.median(f2_vals)

            # Filter: keep entries where both features are on the "winner" side of median
            kept = []
            for t in blocked_entries:
                v1 = t['features'][f1['feature']]
                v2 = t['features'][f2['feature']]
                if pd.isna(v1) or pd.isna(v2):
                    continue
                cond1 = v1 >= f1_med if f1_higher else v1 <= f1_med
                cond2 = v2 >= f2_med if f2_higher else v2 <= f2_med
                if cond1 and cond2:
                    kept.append(t)

            if len(kept) < 10:
                continue

            n_kept = len(kept)
            wr = sum(1 for t in kept if t['winner']) / n_kept * 100
            avg_p = sum(t['pnl_pct'] for t in kept) / n_kept
            sum_p = sum(t['pnl_pct'] for t in kept)
            kept_pct = n_kept / len(blocked_entries) * 100

            print(f"\n  {f1['feature']} {'≥' if f1_higher else '≤'} {f1_med:.3f} "
                  f"AND {f2['feature']} {'≥' if f2_higher else '≤'} {f2_med:.3f}")
            print(f"  → {n_kept} trades ({kept_pct:.0f}% of blocked), "
                  f"WR={wr:.1f}%, Avg={avg_p:+.2f}%, Sum={sum_p:+.1f}%")

# ── ADX(7) specific analysis ─────────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"ADX(7) DEEP DIVE — Short-period ADX as replacement filter")
print(f"{'═'*80}")

adx7_data = [(t['features']['adx_7'], t['pnl_pct'], t['winner'], t['features']['adx_14'])
             for t in blocked_entries
             if not pd.isna(t['features']['adx_7'])]

if adx7_data:
    adx7_vals = np.array([d[0] for d in adx7_data])
    pnls_all  = np.array([d[1] for d in adx7_data])
    wins_all  = np.array([d[2] for d in adx7_data])
    adx14_vals = np.array([d[3] for d in adx7_data])

    print(f"\n  All ADX-blocked entries: n={len(adx7_data)}")
    print(f"  ADX(14) range: {adx14_vals.min():.1f} to {adx14_vals.max():.1f} (all < {ADX_THRESHOLD})")
    print(f"  ADX(7)  range: {adx7_vals.min():.1f} to {adx7_vals.max():.1f}")
    print(f"  ADX(7) ≥ 20:  {(adx7_vals >= 20).sum()} entries ({(adx7_vals >= 20).mean()*100:.1f}%)")
    print(f"  ADX(7) ≥ 15:  {(adx7_vals >= 15).sum()} entries ({(adx7_vals >= 15).mean()*100:.1f}%)")

    print(f"\n  ADX(7) threshold sweep:")
    print(f"  {'Threshold':>10} {'Trades':>7} {'WR':>6} {'AvgPnL':>8} {'SumPnL':>9} {'AvgMFE':>8}")

    for thresh in [10, 12, 14, 15, 16, 18, 20, 22, 25]:
        mask = adx7_vals >= thresh
        n_t = mask.sum()
        if n_t < 5:
            continue
        wr = wins_all[mask].mean() * 100
        avg_p = pnls_all[mask].mean()
        sum_p = pnls_all[mask].sum()

        # MFE
        mfes = [t['mfe_pct'] for t, d in zip(blocked_entries, adx7_data) if d[0] >= thresh]
        avg_mfe = np.mean(mfes) if mfes else 0

        print(f"  ADX(7)≥{thresh:<4d} {n_t:>7d} {wr:>5.1f}% {avg_p:>+7.2f}% {sum_p:>+8.1f}% {avg_mfe:>7.2f}%")

    print(f"  {'ALL':>10} {len(adx7_data):>7d} {wins_all.mean()*100:>5.1f}% "
          f"{pnls_all.mean():>+7.2f}% {pnls_all.sum():>+8.1f}% "
          f"{np.mean([t['mfe_pct'] for t in blocked_entries]):>7.2f}%")

# ── ADX(7) rising analysis ──────────────────────────────────────────────────
print(f"\n  ADX(7) delta (rate of change) analysis:")
adx7d_data = [(t['features']['adx7_delta_3h'], t['features']['adx_7'],
               t['pnl_pct'], t['winner'])
              for t in blocked_entries
              if not pd.isna(t['features']['adx7_delta_3h']) and not pd.isna(t['features']['adx_7'])]

if adx7d_data:
    d3_vals = np.array([d[0] for d in adx7d_data])
    a7_vals = np.array([d[1] for d in adx7d_data])
    pnl_vals = np.array([d[2] for d in adx7d_data])
    win_vals = np.array([d[3] for d in adx7d_data])

    # Combined: ADX(7) >= threshold AND ADX(7) rising (delta > 0)
    print(f"\n  Combined ADX(7) ≥ threshold AND rising (3h delta > 0):")
    print(f"  {'Filter':>25} {'Trades':>7} {'WR':>6} {'AvgPnL':>8} {'SumPnL':>9}")

    for thresh in [12, 14, 15, 16, 18, 20]:
        mask = (a7_vals >= thresh) & (d3_vals > 0)
        n_t = mask.sum()
        if n_t < 5:
            continue
        wr = win_vals[mask].mean() * 100
        avg_p = pnl_vals[mask].mean()
        sum_p = pnl_vals[mask].sum()
        print(f"  ADX7≥{thresh} & rising  {n_t:>7d} {wr:>5.1f}% {avg_p:>+7.2f}% {sum_p:>+8.1f}%")

    # ADX(7) rising only
    mask_rising = d3_vals > 0
    if mask_rising.sum() >= 5:
        wr = win_vals[mask_rising].mean() * 100
        avg_p = pnl_vals[mask_rising].mean()
        sum_p = pnl_vals[mask_rising].sum()
        print(f"  {'ADX7 rising only':>25} {mask_rising.sum():>7d} {wr:>5.1f}% {avg_p:>+7.2f}% {sum_p:>+8.1f}%")

# ── Per-direction breakdown ──────────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"PER-DIRECTION BREAKDOWN")
print(f"{'═'*80}")

for d in ['LONG', 'SHORT']:
    d_trades = [t for t in blocked_entries if t['direction'] == d]
    if not d_trades:
        continue
    d_wins = [t for t in d_trades if t['winner']]
    d_sum = sum(t['pnl_pct'] for t in d_trades)
    print(f"\n  {d}: {len(d_trades)} trades, WR={len(d_wins)/len(d_trades)*100:.1f}%, "
          f"Sum PnL={d_sum:+.1f}%, Avg={d_sum/len(d_trades):+.2f}%")

# ── Per-trigger breakdown ────────────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"PER-TRIGGER BREAKDOWN")
print(f"{'═'*80}")

for trig in ['keltner_breakout', 'keltner_pullback', 'kc_midline_hold']:
    t_trades = [t for t in blocked_entries if t['features']['trigger'] == trig]
    if not t_trades:
        continue
    t_wins = [t for t in t_trades if t['winner']]
    t_sum = sum(t['pnl_pct'] for t in t_trades)
    print(f"\n  {trig}: {len(t_trades)} trades, WR={len(t_wins)/len(t_trades)*100:.1f}%, "
          f"Sum PnL={t_sum:+.1f}%, Avg={t_sum/len(t_trades):+.2f}%")

# ── Exit reason distribution ─────────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"EXIT REASON DISTRIBUTION")
print(f"{'═'*80}")

exit_reasons = {}
for t in blocked_entries:
    er = t['exit_reason']
    if er not in exit_reasons:
        exit_reasons[er] = {'count': 0, 'pnl': 0, 'wins': 0}
    exit_reasons[er]['count'] += 1
    exit_reasons[er]['pnl'] += t['pnl_pct']
    exit_reasons[er]['wins'] += 1 if t['winner'] else 0

for er, data in sorted(exit_reasons.items(), key=lambda x: -x[1]['count']):
    wr = data['wins'] / data['count'] * 100
    avg = data['pnl'] / data['count']
    print(f"  {er:<20}  {data['count']:>4d} trades  WR={wr:>5.1f}%  "
          f"Avg={avg:>+6.2f}%  Sum={data['pnl']:>+8.1f}%")

# ── Hold time distribution ───────────────────────────────────────────────────
print(f"\n{'═'*80}")
print(f"HOLD TIME vs OUTCOME")
print(f"{'═'*80}")

for label, lo, hi in [('<6h', 0, 6), ('6-12h', 6, 12), ('12-24h', 12, 24),
                       ('1-2d', 24, 48), ('2-4d', 48, 96), ('>4d', 96, 9999)]:
    bucket = [t for t in blocked_entries if lo <= t['bars_held'] < hi]
    if not bucket:
        continue
    wr = sum(1 for t in bucket if t['winner']) / len(bucket) * 100
    s = sum(t['pnl_pct'] for t in bucket)
    print(f"  {label:<8}  {len(bucket):>4d} trades  WR={wr:>5.1f}%  Sum={s:>+8.1f}%")

print(f"\n{'═'*80}")
print(f"Done. ({LABEL})")
print(f"{'═'*80}")
