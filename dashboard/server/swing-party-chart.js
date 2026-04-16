/**
 * Compute SwingParty chart data server-side.
 *
 * Same logic as the Python reporter (swing_party_reporter.py):
 *   - Per-asset normalized % from first bar in view
 *   - Solid segments when asset is "held" (in book), dotted when not
 *   - Portfolio value area chart from trade log
 *   - Per-asset Supertrend (normalized %) and volume for hover overlays
 *
 * Input: multi-asset OHLCV + trade log rows
 * Output: JSON bundle identical to build_swing_party_chart_data
 */

import { computeSupertrendFromRaw } from './supertrend.js';

const ASSET_LINE_COLORS = [
  '#38bdf8', '#f472b6', '#a3e635', '#fbbf24',
  '#c084fc', '#2dd4bf', '#fb923c', '#94a3b8',
];

/**
 * Build the per-timeframe chart payload.
 *
 * @param {Object<string, Array>} assetOhlcv  - { TSLA: [{timestamp,open,high,low,close,volume},...], ... }
 * @param {Array} tradeRows                   - trade log rows (date,action,symbol,qty,price,cashBalance,portfolioValue,...)
 * @param {string|null} freq                  - resample: null=raw 5m, "1h", "4h"
 * @param {Object} stParams                   - { atrPeriod, multiplier, resampleInterval }
 * @returns {Object<string, {solid, dotted, st, volume}>}
 */
function tfChartPayload(assetOhlcv, tradeRows, freq, stParams) {
  const symbols = Object.keys(assetOhlcv).sort();

  // Resample each asset's OHLCV to `freq`
  const resampled = {};
  for (const sym of symbols) {
    resampled[sym] = freq ? resample(assetOhlcv[sym], freq) : assetOhlcv[sym];
  }

  // Union bar timestamps
  const tsSet = new Set();
  for (const sym of symbols) {
    for (const row of resampled[sym]) tsSet.add(row.timestamp);
  }
  const barTimes = Array.from(tsSet).sort((a, b) => a - b);
  if (barTimes.length === 0) return {};

  // Per-asset: aligned close, pct, held flags, ST, volume
  const out = {};
  for (const sym of symbols) {
    const closeMap = new Map();
    const volMap = new Map();
    for (const row of resampled[sym]) {
      closeMap.set(row.timestamp, row.close);
      volMap.set(row.timestamp, row.volume);
    }

    // Forward-fill close across union timestamps
    const closes = [];
    let lastClose = null;
    for (const ts of barTimes) {
      if (closeMap.has(ts)) lastClose = closeMap.get(ts);
      closes.push(lastClose);
    }

    // Normalize to % from first non-null
    const firstIdx = closes.findIndex(c => c !== null && c > 0);
    const base = firstIdx >= 0 ? closes[firstIdx] : 1;
    const pct = closes.map(c => c !== null ? ((c / base) - 1) * 100 : null);

    // Held flags at each bar
    const held = heldFlagsAtBarTimes(tradeRows, sym, barTimes, freq);

    // Supertrend — compute on raw 5m, forward-fill to display timeframe
    let stPoints = [];
    if (stParams) {
      const raw5m = assetOhlcv[sym] || [];
      const stRaw = computeSupertrendFromRaw(
        raw5m,
        stParams.atrPeriod,
        stParams.multiplier,
        resampled[sym],
        stParams.resampleInterval,
      );
      // Normalize ST the same way as price (% from same base)
      stPoints = stRaw.map(p => ({
        time: Math.floor(p.time / 1000),
        value: Math.round(((p.value / base) - 1) * 100 * 10000) / 10000,
        color: p.color,
      }));
    }

    // Volume at each bar
    const volume = barTimes
      .filter(ts => volMap.has(ts))
      .map(ts => ({
        time: Math.floor(ts / 1000),
        value: volMap.get(ts),
      }));

    out[sym] = {
      solid: segmentPoints(barTimes, pct, held, true),
      dotted: segmentPoints(barTimes, pct, held, false),
      st: stPoints,
      volume,
    };
  }

  return out;
}

/**
 * Determine whether `symbol` is held at each bar timestamp.
 * Uses merge-asof logic: for each bar, find the last trade for this symbol
 * at or before bar end, check if position_qty > 0 or short_qty > 0.
 */
function heldFlagsAtBarTimes(tradeRows, symbol, barTimes, freq) {
  const posAfterTrade = tradeRows
    .filter(t => t.symbol === symbol)
    .map(t => {
      const raw = t._raw || t;
      const posQty = parseFloat(raw.position_qty || raw.positionQty || 0);
      const shortQty = parseFloat(raw.short_qty || raw.shortQty || 0);
      return {
        ts: new Date(t.date).getTime(),
        held: posQty > 0 || shortQty > 0,
      };
    })
    .sort((a, b) => a.ts - b.ts);

  if (posAfterTrade.length === 0) {
    return barTimes.map(() => false);
  }

  const barDelta = freqMs(freq);

  return barTimes.map(barTs => {
    const evalEnd = barTs + barDelta - 1;
    // Binary search: last trade with ts <= evalEnd
    let lo = 0, hi = posAfterTrade.length - 1, best = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (posAfterTrade[mid].ts <= evalEnd) {
        best = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return best >= 0 ? posAfterTrade[best].held : false;
  });
}

function freqMs(freq) {
  if (freq === '1h') return 3600_000;
  if (freq === '4h') return 4 * 3600_000;
  return 5 * 60_000; // 5m default
}

/**
 * Break a line into contiguous solid or dotted segments.
 */
function segmentPoints(barTimes, pct, held, wantHeld) {
  const segs = [];
  let cur = [];

  for (let i = 0; i < barTimes.length; i++) {
    const v = pct[i];
    if (v === null || v === undefined || isNaN(v)) {
      if (cur.length) { segs.push(cur); cur = []; }
      continue;
    }
    if (held[i] !== wantHeld) {
      if (cur.length) { segs.push(cur); cur = []; }
      continue;
    }
    cur.push({
      time: Math.floor(barTimes[i] / 1000),
      value: Math.round(v * 10000) / 10000,
    });
  }
  if (cur.length) segs.push(cur);
  return segs;
}

/**
 * Resample raw 5m OHLCV to 1h or 4h.
 */
function resample(rows, freq) {
  const intervalMs = freqMs(freq);
  if (rows.length === 0) return [];
  const result = [];
  let bucket = null, bucketStart = 0;

  for (const d of rows) {
    const start = Math.floor(d.timestamp / intervalMs) * intervalMs;
    if (bucket && start === bucketStart) {
      bucket.high = Math.max(bucket.high, d.high);
      bucket.low = Math.min(bucket.low, d.low);
      bucket.close = d.close;
      bucket.volume += d.volume;
    } else {
      if (bucket) result.push(bucket);
      bucketStart = start;
      bucket = { timestamp: start, open: d.open, high: d.high, low: d.low, close: d.close, volume: d.volume };
    }
  }
  if (bucket) result.push(bucket);
  return result;
}

/**
 * Build portfolio value series from trade log.
 */
function buildPortfolio(tradeRows) {
  if (!tradeRows.length) return [];
  // tradeRows are most-recent-first from readTradeLog — reverse to chronological
  const chronological = [...tradeRows].reverse();
  const seen = new Set();
  const result = [];
  for (const t of chronological) {
    const ts = Math.floor(new Date(t.date).getTime() / 1000);
    if (seen.has(ts) || isNaN(ts)) continue;
    seen.add(ts);
    result.push({ time: ts, value: t.portfolioValue });
  }
  return result;
}

/**
 * Latest snapshot per symbol from a newest-first trade log (see readTradeLogWithRaw).
 * First row per symbol is the most recent trade.
 */
function currentSnapshotBySymbol(tradeRows) {
  const snap = {};
  for (const t of tradeRows) {
    const sym = t.symbol;
    if (!sym || Object.prototype.hasOwnProperty.call(snap, sym)) continue;
    const raw = t._raw || t;
    const posQty = parseFloat(raw.position_qty ?? raw.positionQty ?? t.position_qty ?? 0);
    const shortQty = parseFloat(raw.short_qty ?? raw.shortQty ?? t.short_qty ?? 0);
    const posAvg = parseFloat(raw.position_avg_cost ?? raw.positionAvgCost ?? t.position_avg_cost ?? 0);
    const shortAvg = parseFloat(raw.short_avg_cost ?? raw.shortAvgCost ?? t.short_avg_cost ?? 0);
    if (shortQty > 0) snap[sym] = { side: 'short', avgCost: shortAvg };
    else if (posQty > 0) snap[sym] = { side: 'long', avgCost: posAvg };
    else snap[sym] = { side: null, avgCost: null };
  }
  return snap;
}

function lastCloseFromOhlcv(rows) {
  if (!rows?.length) return null;
  const last = rows[rows.length - 1];
  const c = last.close;
  if (c == null) return null;
  const n = typeof c === 'number' ? c : parseFloat(c);
  return Number.isFinite(n) ? n : null;
}

function positionPnlPct(side, avgCost, lastPrice) {
  if (lastPrice == null || !avgCost || avgCost <= 0) return null;
  if (side === 'long') return ((lastPrice - avgCost) / avgCost) * 100;
  if (side === 'short') return ((avgCost - lastPrice) / avgCost) * 100;
  return null;
}

/**
 * Main entry: build full chart data bundle for a SwingParty bot.
 *
 * @param {Object<string, Array>} assetOhlcv - per-symbol raw 5m OHLCV rows
 * @param {Array} tradeRows - trade log rows from readTradeLog (with _raw)
 * @param {Object} stParams - { atrPeriod, multiplier, resampleInterval }
 * @returns {{ "5m": Object, "1h": Object, "4h": Object, portfolio: Array, symbols: Array }}
 */
export function buildSwingPartyChartData(assetOhlcv, tradeRows, stParams) {
  const symbols = Object.keys(assetOhlcv).sort();
  const snapMap = currentSnapshotBySymbol(tradeRows);
  const meta = symbols.map((sym, i) => {
    const sn = snapMap[sym] || { side: null, avgCost: null };
    const lastPrice = lastCloseFromOhlcv(assetOhlcv[sym]);
    const rawPnl = positionPnlPct(sn.side, sn.avgCost, lastPrice);
    const pnlPct = rawPnl != null && Number.isFinite(rawPnl) ? Math.round(rawPnl * 100) / 100 : null;
    return {
      symbol: sym,
      color: ASSET_LINE_COLORS[i % ASSET_LINE_COLORS.length],
      side: sn.side,
      lastPrice,
      pnlPct,
    };
  });

  return {
    '5m': tfChartPayload(assetOhlcv, tradeRows, null, stParams),
    '1h': tfChartPayload(assetOhlcv, tradeRows, '1h', stParams),
    '4h': tfChartPayload(assetOhlcv, tradeRows, '4h', stParams),
    portfolio: buildPortfolio(tradeRows),
    symbols: meta,
  };
}

/**
 * Read trade log with raw columns preserved for held-state computation.
 */
export async function readTradeLogWithRaw(filePath, count = 5000) {
  const { createReadStream } = await import('node:fs');
  const csvParse = await import('csv-parse');
  const parse = csvParse.parse;

  return new Promise((resolve, reject) => {
    const rows = [];
    const stream = createReadStream(filePath);
    stream.on('error', reject);
    stream
      .pipe(parse({ columns: true, skip_empty_lines: true, relax_quotes: true, relax_column_count: true }))
      .on('data', (row) => {
        rows.push({
          date: row.date || '',
          action: row.action || '',
          symbol: row.symbol || '',
          qty: parseFloat(row.quantity) || 0,
          price: parseFloat(row.price) || 0,
          cashBalance: parseFloat(row.cash_balance) || 0,
          portfolioValue: parseFloat(row.portfolio_value) || 0,
          position_qty: parseFloat(row.position_qty) || 0,
          position_avg_cost: parseFloat(row.position_avg_cost) || 0,
          short_qty: parseFloat(row.short_qty) || 0,
          short_avg_cost: parseFloat(row.short_avg_cost) || 0,
        });
      })
      .on('end', () => resolve(rows.slice(-count).reverse()))
      .on('error', reject);
  });
}
