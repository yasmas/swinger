/**
 * Trade table rows for the dashboard — mirrors `build_trade_table_rows` in
 * `src/reporting/swing_party_reporter.py` (realized PnL on SELL/COVER, FIFO lots,
 * highlight bounds). HOLD rows are skipped.
 */

const EPS = 1e-12;

/**
 * POSIX seconds for a trade-log timestamp. Live bots write naive *local* time
 * (see SwingBot/SwingPartyBot `_log_trade` callers using `datetime.now()`), so
 * naive strings are parsed as local wall clock — not UTC. Strings with an
 * explicit offset/Z are parsed as written.
 */
export function posixUtcSeconds(dateStr) {
  const s = String(dateStr || '').trim();
  if (!s) return 0;
  if (/[zZ]$|([+-]\d{2}:?\d{2})$/.test(s)) {
    const ms = Date.parse(s);
    return Number.isNaN(ms) ? 0 : Math.floor(ms / 1000);
  }
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) {
    const ms = Date.parse(s);
    return Number.isNaN(ms) ? 0 : Math.floor(ms / 1000);
  }
  const [, y, mo, d, hh, mm, ss] = m;
  const dt = new Date(
    Number(y), Number(mo) - 1, Number(d),
    Number(hh), Number(mm), Number(ss || '0'),
  );
  return Math.floor(dt.getTime() / 1000);
}

/**
 * @param {Array<{date: string, action: string, symbol: string, qty: number, price: number, portfolioValue: number}>} chronologicalRows
 *        Oldest first, same shape as parsed trade log rows.
 * @returns {Array<object>}
 */
export function buildTradeTableRows(chronologicalRows) {
  const longQty = Object.create(null);
  const longAvg = Object.create(null);
  const shortQty = Object.create(null);
  const shortAvg = Object.create(null);
  /** @type {Record<string, Array<[number, number, number]>>} symbol -> FIFO [qty_left, open_unix, out_row_index] */
  const longLots = Object.create(null);
  const shortLots = Object.create(null);

  const out = [];

  if (!chronologicalRows.length) return out;

  let maxTsEnd = 0;
  for (const r of chronologicalRows) {
    const u = posixUtcSeconds(r.date);
    if (u > maxTsEnd) maxTsEnd = u;
  }

  function ensureLots(map, sym) {
    if (!map[sym]) map[sym] = [];
    return map[sym];
  }

  for (const r of chronologicalRows) {
    const act = String(r.action || '').toUpperCase();
    if (act === 'HOLD') continue;
    if (!['BUY', 'SELL', 'SHORT', 'COVER'].includes(act)) continue;

    const sym = String(r.symbol || '');
    const qty = Math.abs(r.qty) || 0;
    const px = r.price;
    const cs = r.contractSize || 1;
    const timeUnix = posixUtcSeconds(r.date);
    const notional = Math.abs(qty * px * cs);

    let pvClose = null;
    if (act === 'SELL' || act === 'COVER') {
      pvClose = r.portfolioValue;
    }

    let pnlDollar = null;
    let pnlPct = null;

    if (act === 'BUY') {
      const lq = longQty[sym] || 0;
      const la = longAvg[sym] || 0;
      const newLq = lq + qty;
      const newLa = newLq > 0 ? (la * lq + px * qty) / newLq : 0;
      longQty[sym] = newLq;
      longAvg[sym] = newLa;
      const rowIdx = out.length;
      out.push({
        timeUnix,
        tradeType: act,
        ticker: sym,
        qty,
        price: px,
        value: notional,
        pnlPct,
        pnlDollar,
        portfolioValue: pvClose,
        highlightStartUnix: timeUnix,
        highlightEndUnix: null,
        date: r.date,
      });
      ensureLots(longLots, sym).push([qty, timeUnix, rowIdx]);
    } else if (act === 'SELL') {
      const la = longAvg[sym] || 0;
      pnlDollar = qty * (px - la) * cs;
      pnlPct = la > 0 ? ((px - la) / la) * 100 : null;
      let lq = longQty[sym] || 0;
      lq -= qty;
      if (lq <= EPS) {
        delete longQty[sym];
        delete longAvg[sym];
      } else {
        longQty[sym] = lq;
      }

      let remaining = qty;
      let firstOpenT = null;
      const q = ensureLots(longLots, sym);
      while (remaining > EPS && q.length > 0) {
        const lot = q[0];
        if (firstOpenT === null) firstOpenT = lot[1];
        const take = Math.min(remaining, lot[0]);
        lot[0] -= take;
        remaining -= take;
        if (lot[0] <= EPS) {
          q.shift();
          out[lot[2]].highlightEndUnix = timeUnix;
        }
      }

      out.push({
        timeUnix,
        tradeType: act,
        ticker: sym,
        qty,
        price: px,
        value: notional,
        pnlPct,
        pnlDollar,
        portfolioValue: pvClose,
        highlightStartUnix: firstOpenT != null ? firstOpenT : timeUnix,
        highlightEndUnix: timeUnix,
        date: r.date,
      });
    } else if (act === 'SHORT') {
      const sq = shortQty[sym] || 0;
      const sa = shortAvg[sym] || 0;
      const newSq = sq + qty;
      const newSa = newSq > 0 ? (sa * sq + px * qty) / newSq : 0;
      shortQty[sym] = newSq;
      shortAvg[sym] = newSa;
      const rowIdx = out.length;
      out.push({
        timeUnix,
        tradeType: act,
        ticker: sym,
        qty,
        price: px,
        value: notional,
        pnlPct,
        pnlDollar,
        portfolioValue: pvClose,
        highlightStartUnix: timeUnix,
        highlightEndUnix: null,
        date: r.date,
      });
      ensureLots(shortLots, sym).push([qty, timeUnix, rowIdx]);
    } else if (act === 'COVER') {
      const sa = shortAvg[sym] || 0;
      pnlDollar = qty * (sa - px) * cs;
      pnlPct = sa > 0 ? ((sa - px) / sa) * 100 : null;
      let sq = shortQty[sym] || 0;
      sq -= qty;
      if (sq <= EPS) {
        delete shortQty[sym];
        delete shortAvg[sym];
      } else {
        shortQty[sym] = sq;
      }

      let remaining = qty;
      let firstOpenT = null;
      const q = ensureLots(shortLots, sym);
      while (remaining > EPS && q.length > 0) {
        const lot = q[0];
        if (firstOpenT === null) firstOpenT = lot[1];
        const take = Math.min(remaining, lot[0]);
        lot[0] -= take;
        remaining -= take;
        if (lot[0] <= EPS) {
          q.shift();
          out[lot[2]].highlightEndUnix = timeUnix;
        }
      }

      out.push({
        timeUnix,
        tradeType: act,
        ticker: sym,
        qty,
        price: px,
        value: notional,
        pnlPct,
        pnlDollar,
        portfolioValue: pvClose,
        highlightStartUnix: firstOpenT != null ? firstOpenT : timeUnix,
        highlightEndUnix: timeUnix,
        date: r.date,
      });
    }
  }

  for (const row of out) {
    if (row.highlightEndUnix == null) {
      row.highlightEndUnix = maxTsEnd;
    }
  }

  return out;
}
