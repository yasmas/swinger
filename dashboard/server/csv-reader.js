/**
 * CSV reader for trade logs and OHLCV price data.
 */

import { createReadStream } from 'fs';
import { readdir, stat } from 'fs/promises';
import path from 'path';
import { parse } from 'csv-parse';

/**
 * Read the trade log CSV, returning the last `count` rows.
 */
export async function readTradeLog(filePath, count = 100) {
  const rows = [];

  return new Promise((resolve, reject) => {
    createReadStream(filePath)
      .pipe(parse({ columns: true, skip_empty_lines: true, relax_quotes: true }))
      .on('data', (row) => {
        rows.push({
          date: row.date || '',
          action: row.action || '',
          symbol: row.symbol || '',
          qty: parseFloat(row.quantity) || 0,
          price: parseFloat(row.price) || 0,
          cashBalance: parseFloat(row.cash_balance) || 0,
          portfolioValue: parseFloat(row.portfolio_value) || 0,
          details: tryParseJSON(row.details),
        });
      })
      .on('end', () => {
        // Return last `count` rows, most recent first
        resolve(rows.slice(-count).reverse());
      })
      .on('error', (err) => {
        reject(err);
      });
  });
}

/**
 * Read OHLCV monthly CSV files from a data directory.
 * Returns data for the specified range.
 *
 * @param {string} dataDir - Directory containing monthly CSV files
 * @param {string} symbol - Symbol name (e.g., "BTCUSDT")
 * @param {string} interval - Interval (e.g., "5m")
 * @param {string} range - Time range: "1W", "1M", "3M", "6M", "1Y"
 */
export async function readOHLCV(dataDir, symbol, interval, range = '1M') {
  const rangeDays = { '1W': 7, '1M': 30, '3M': 90, '6M': 180, '1Y': 365 }[range] || 30;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - rangeDays);

  // Find relevant monthly CSV files
  const files = await findMonthlyFiles(dataDir, symbol, interval);

  // Filter to files that may contain data in our range
  const relevantFiles = files.filter(f => {
    // File names like BTCUSDT-5m-2025-03.csv
    const match = f.match(/(\d{4})-(\d{2})\.csv$/);
    if (!match) return false;
    const fileDate = new Date(parseInt(match[1]), parseInt(match[2]) - 1, 1);
    // Include if file's month is >= cutoff month (with 1-month buffer)
    const bufferDate = new Date(cutoff);
    bufferDate.setMonth(bufferDate.getMonth() - 1);
    return fileDate >= bufferDate;
  });

  const allRows = [];

  for (const file of relevantFiles) {
    const filePath = path.join(dataDir, file);
    const rows = await readCSVFile(filePath);
    allRows.push(...rows);
  }

  // Filter by cutoff and format
  const cutoffMs = cutoff.getTime();
  const filtered = allRows
    .filter(r => r.timestamp >= cutoffMs)
    .sort((a, b) => a.timestamp - b.timestamp);

  // Downsample if too many points (target ~500 points for charts)
  const maxPoints = 500;
  if (filtered.length > maxPoints) {
    const step = Math.ceil(filtered.length / maxPoints);
    return filtered.filter((_, i) => i % step === 0);
  }

  return filtered;
}

async function findMonthlyFiles(dataDir, symbol, interval) {
  try {
    const entries = await readdir(dataDir);
    const pattern = `${symbol}-${interval}-`;
    return entries
      .filter(f => f.startsWith(pattern) && f.endsWith('.csv'))
      .sort();
  } catch {
    return [];
  }
}

async function readCSVFile(filePath) {
  return new Promise((resolve, reject) => {
    const rows = [];
    createReadStream(filePath)
      .pipe(parse({ skip_empty_lines: true }))
      .on('data', (cols) => {
        // Expected columns: timestamp, open, high, low, close, volume
        const ts = parseInt(cols[0]);
        if (isNaN(ts)) return; // skip header
        rows.push({
          timestamp: ts,
          date: new Date(ts).toISOString(),
          open: parseFloat(cols[1]) || 0,
          high: parseFloat(cols[2]) || 0,
          low: parseFloat(cols[3]) || 0,
          close: parseFloat(cols[4]) || 0,
          volume: parseFloat(cols[5]) || 0,
        });
      })
      .on('end', () => resolve(rows))
      .on('error', reject);
  });
}

function tryParseJSON(str) {
  try {
    return JSON.parse(str || '{}');
  } catch {
    return {};
  }
}
