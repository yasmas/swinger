/**
 * Supertrend computation on OHLCV data.
 *
 * Resamples raw 5m bars to 1h, computes Supertrend (ATR-based bands + direction),
 * then resamples the ST line to match the requested output timeframe.
 *
 * Returns [{time: <epoch_ms>, value: <number>, color: <string>}, ...]
 */

const ST_BULL_COLOR = '#26a69a';
const ST_BEAR_COLOR = '#ef5350';

/**
 * Resample raw 5m OHLCV rows to hourly bars.
 * Input: [{timestamp, open, high, low, close, volume}, ...]
 * Output: same shape, one row per hour.
 */
function resampleToHourly(rows) {
  if (rows.length === 0) return [];
  const HOUR_MS = 3600_000;
  const buckets = new Map();

  for (const r of rows) {
    const key = Math.floor(r.timestamp / HOUR_MS) * HOUR_MS;
    const b = buckets.get(key);
    if (b) {
      if (r.high > b.high) b.high = r.high;
      if (r.low < b.low) b.low = r.low;
      b.close = r.close;
      b.volume += r.volume;
    } else {
      buckets.set(key, {
        timestamp: key,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
        volume: r.volume,
      });
    }
  }

  return Array.from(buckets.values()).sort((a, b) => a.timestamp - b.timestamp);
}

/**
 * Compute ATR using Wilder's smoothing (RMA).
 */
function computeATR(hourly, period) {
  const n = hourly.length;
  const tr = new Float64Array(n);
  const atr = new Float64Array(n);

  tr[0] = hourly[0].high - hourly[0].low;
  for (let i = 1; i < n; i++) {
    const h = hourly[i].high;
    const l = hourly[i].low;
    const pc = hourly[i - 1].close;
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
  }

  // SMA for first `period` bars
  let sum = 0;
  for (let i = 0; i < Math.min(period, n); i++) sum += tr[i];
  if (n >= period) {
    atr[period - 1] = sum / period;
    for (let i = period; i < n; i++) {
      atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period;
    }
  }

  return atr;
}

/**
 * Compute Supertrend indicator.
 * Returns { stLine: Float64Array, isBullish: Uint8Array }
 */
function computeSupertrend(hourly, atrPeriod, multiplier) {
  const n = hourly.length;
  const atr = computeATR(hourly, atrPeriod);

  const finalUpper = new Float64Array(n);
  const finalLower = new Float64Array(n);
  const stLine = new Float64Array(n);
  const isBullish = new Uint8Array(n); // 1 = bullish

  // Raw bands
  for (let i = 0; i < n; i++) {
    const hl2 = (hourly[i].high + hourly[i].low) / 2;
    finalUpper[i] = hl2 + multiplier * atr[i];
    finalLower[i] = hl2 - multiplier * atr[i];
  }

  isBullish[0] = 1;

  for (let i = 1; i < n; i++) {
    if (atr[i] === 0) {
      isBullish[i] = isBullish[i - 1];
      continue;
    }

    const prevClose = hourly[i - 1].close;

    // Upper band: can only decrease (tighten)
    if (!(finalUpper[i] < finalUpper[i - 1] || prevClose > finalUpper[i - 1])) {
      finalUpper[i] = finalUpper[i - 1];
    }

    // Lower band: can only increase (tighten)
    if (!(finalLower[i] > finalLower[i - 1] || prevClose < finalLower[i - 1])) {
      finalLower[i] = finalLower[i - 1];
    }

    const close = hourly[i].close;

    if (isBullish[i - 1]) {
      if (close < finalLower[i]) {
        isBullish[i] = 0;
        stLine[i] = finalUpper[i];
      } else {
        isBullish[i] = 1;
        stLine[i] = finalLower[i];
      }
    } else {
      if (close > finalUpper[i]) {
        isBullish[i] = 1;
        stLine[i] = finalLower[i];
      } else {
        isBullish[i] = 0;
        stLine[i] = finalUpper[i];
      }
    }
  }

  // First bar
  stLine[0] = isBullish[0] ? finalLower[0] : finalUpper[0];

  return { stLine, isBullish };
}

/**
 * Compute Supertrend from raw 5m OHLCV data.
 *
 * @param {Array} raw5m - Raw 5m OHLCV rows [{timestamp, open, high, low, close, volume}]
 * @param {number} atrPeriod - ATR period (default 20)
 * @param {number} multiplier - Supertrend multiplier (default 2.5)
 * @param {Array} ohlcvOutput - The OHLCV data being sent to the client (to match timestamps)
 * @returns {Array} [{time: <epoch_ms>, value: <number>, color: <string>}]
 */
export function computeSupertrendFromRaw(raw5m, atrPeriod = 20, multiplier = 2.5, ohlcvOutput = null) {
  const hourly = resampleToHourly(raw5m);
  if (hourly.length < atrPeriod + 2) return [];

  const { stLine, isBullish } = computeSupertrend(hourly, atrPeriod, multiplier);

  // Build hourly ST data points (skip warmup where ATR is 0)
  const hourlyPoints = [];
  for (let i = atrPeriod; i < hourly.length; i++) {
    if (stLine[i] === 0) continue;
    hourlyPoints.push({
      timestamp: hourly[i].timestamp,
      value: Math.round(stLine[i] * 100) / 100,
      bull: isBullish[i] === 1,
    });
  }

  if (!ohlcvOutput || ohlcvOutput.length === 0) {
    // Return hourly ST points directly
    return hourlyPoints.map(p => ({
      time: p.timestamp,
      value: p.value,
      color: p.bull ? ST_BULL_COLOR : ST_BEAR_COLOR,
    }));
  }

  // Forward-fill ST to match the output OHLCV timestamps
  const result = [];
  let stIdx = 0;

  for (const candle of ohlcvOutput) {
    const t = candle.timestamp;

    // Advance ST index to the latest hourly point <= this candle's timestamp
    while (stIdx < hourlyPoints.length - 1 && hourlyPoints[stIdx + 1].timestamp <= t) {
      stIdx++;
    }

    if (stIdx < hourlyPoints.length && hourlyPoints[stIdx].timestamp <= t) {
      const p = hourlyPoints[stIdx];
      result.push({
        time: t,
        value: p.value,
        color: p.bull ? ST_BULL_COLOR : ST_BEAR_COLOR,
      });
    }
  }

  return result;
}
