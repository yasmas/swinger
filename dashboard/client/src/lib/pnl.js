/**
 * Client-side PnL computation from trade data.
 */

/**
 * Compute PnL stats from a list of trades.
 * Trades are expected to have: date, action, price, qty, portfolioValue
 *
 * @param {Array} trades - Trade rows from API (most recent first)
 * @param {number} initialCash - Starting portfolio value
 * @returns {Object} { ytd, mtd, wtd, portfolioHistory, pnlByWeek }
 */
export function computePnlStats(trades, initialCash = 100000) {
  const list = Array.isArray(trades) ? trades : [];
  if (list.length === 0) {
    return { ytd: 0, mtd: 0, wtd: 0, portfolioHistory: [], pnlByWeek: [] };
  }

  // Trades come most-recent-first from API; reverse for chronological
  const chronological = [...list].reverse();
  const now = new Date();

  const startOfYear = new Date(now.getFullYear(), 0, 1);
  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
  const dayOfWeek = now.getDay();
  const startOfWeek = new Date(now);
  startOfWeek.setDate(now.getDate() - (dayOfWeek === 0 ? 6 : dayOfWeek - 1));
  startOfWeek.setHours(0, 0, 0, 0);

  // Find portfolio values at period boundaries
  const currentValue = chronological.length > 0 ? chronological[chronological.length - 1].portfolioValue : initialCash;

  function findValueAt(targetDate) {
    // Find the last trade on or before targetDate
    for (let i = chronological.length - 1; i >= 0; i--) {
      const tradeDate = new Date(chronological[i].date);
      if (tradeDate <= targetDate) {
        return chronological[i].portfolioValue;
      }
    }
    return initialCash;
  }

  const ytdBase = findValueAt(startOfYear) || initialCash;
  const mtdBase = findValueAt(startOfMonth) || ytdBase;
  const wtdBase = findValueAt(startOfWeek) || mtdBase;

  const ytd = ((currentValue - ytdBase) / ytdBase) * 100;
  const mtd = ((currentValue - mtdBase) / mtdBase) * 100;
  const wtd = ((currentValue - wtdBase) / wtdBase) * 100;

  // Portfolio history (all portfolio_value entries)
  const portfolioHistory = chronological.map(t => ({
    date: formatShortDate(new Date(t.date)),
    value: t.portfolioValue,
  }));

  // PnL by week — pair entries/exits for weekly PnL computation
  const pnlByWeek = computeWeeklyPnl(chronological);

  return { ytd, mtd, wtd, portfolioHistory, pnlByWeek };
}

/**
 * Compute weekly PnL from paired entry/exit trades.
 */
function computeWeeklyPnl(trades) {
  const weeks = new Map();

  // Pair BUY/SHORT with SELL/COVER — keyed by symbol to handle concurrent positions
  const openBySymbol = new Map(); // symbol → [entry, ...]
  const completedTrades = [];

  for (const trade of trades) {
    if (trade.action === 'BUY' || trade.action === 'SHORT') {
      if (!openBySymbol.has(trade.symbol)) openBySymbol.set(trade.symbol, []);
      openBySymbol.get(trade.symbol).push(trade);
    } else if (trade.action === 'SELL' || trade.action === 'COVER') {
      const queue = openBySymbol.get(trade.symbol);
      const entry = queue && queue.shift();
      if (entry) {
        const isLong = entry.action === 'BUY';
        const pnlPct = isLong
          ? ((trade.price - entry.price) / entry.price) * 100
          : ((entry.price - trade.price) / entry.price) * 100;

        completedTrades.push({
          exitDate: new Date(trade.date),
          type: isLong ? 'LONG' : 'SHORT',
          pnlPct,
        });
      }
    }
  }

  // Group by week
  for (const ct of completedTrades) {
    const weekStart = getWeekStart(ct.exitDate);
    const key = weekStart.toISOString().split('T')[0];

    if (!weeks.has(key)) {
      weeks.set(key, { week: formatShortDate(weekStart), longPnl: 0, shortPnl: 0, wins: 0, total: 0 });
    }

    const w = weeks.get(key);
    if (ct.type === 'LONG') {
      w.longPnl += ct.pnlPct;
    } else {
      w.shortPnl += ct.pnlPct;
    }
    if (ct.pnlPct > 0) w.wins++;
    w.total++;
  }

  // Convert to array and compute win rate
  return Array.from(weeks.values())
    .sort((a, b) => a.week.localeCompare(b.week))
    .map(w => ({
      ...w,
      longPnl: Math.round(w.longPnl * 100) / 100,
      shortPnl: Math.round(w.shortPnl * 100) / 100,
      winRate: w.total > 0 ? Math.round((w.wins / w.total) * 1000) / 10 : 0,
    }))
    .slice(-13); // last 13 weeks
}

function getWeekStart(date) {
  const d = new Date(date);
  const day = d.getDay();
  d.setDate(d.getDate() - (day === 0 ? 6 : day - 1));
  d.setHours(0, 0, 0, 0);
  return d;
}

function formatShortDate(date) {
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
