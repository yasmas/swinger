/**
 * REST API routes for the dashboard.
 * All routes require authentication (enforced by middleware in index.js).
 * Bot operations are scoped to the authenticated user.
 */

import { Router } from 'express';
import { readDiagnostics, readFullTradeLogChronological, readOHLCV, readRawOHLCV } from '../csv-reader.js';
import { buildTradeTableRows } from '../trade-table-rows.js';
import { computeSupertrendFromRaw } from '../supertrend.js';
import { buildSwingPartyChartData, readTradeLogWithRaw } from '../swing-party-chart.js';
import path from 'path';
import { readFileSync, existsSync, statSync, readdirSync } from 'fs';
import { readFile } from 'fs/promises';
import YAML from 'yaml';

export function createApiRouter(botStateManager, zmqBridge, processManager, projectRoot) {
  const router = Router();

  function requireAdmin(req, res) {
    if (!req.user?.admin) {
      res.status(403).json({ error: 'Admin only' });
      return false;
    }
    return true;
  }

  function getUserBot(req, res) {
    const bot = botStateManager.getBotForUser(req.params.name, req.user.username);
    if (!bot) {
      res.status(404).json({ error: 'Bot not found' });
      return null;
    }
    return bot;
  }

  // ── Bot List ──────────────────────────────────────────────────────

  router.get('/bots', (req, res) => {
    res.json(botStateManager.getBotsForUser(req.user.username));
  });

  router.get('/bots/:name', (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;
    res.json(bot.toJSON());
  });

  // ── Bot Control ───────────────────────────────────────────────────

  router.post('/bots/:name/start', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;
    try {
      const result = await processManager.startBot(req.params.name);
      res.json(result);
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  router.post('/bots/:name/stop', async (req, res) => {
    if (!requireAdmin(req, res)) return;
    const bot = getUserBot(req, res);
    if (!bot) return;
    try {
      const result = await processManager.stopBot(req.params.name);
      res.json(result);
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  router.post('/bots/:name/pause', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;
    if (bot.status !== 'running') return res.status(400).json({ error: 'Bot not running' });

    await zmqBridge.sendToBot(req.params.name, { type: 'pause' });
    res.json({ ok: true, message: 'Pause command sent' });
  });

  router.post('/bots/:name/resume', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;
    if (bot.status !== 'running') return res.status(400).json({ error: 'Bot not running' });

    await zmqBridge.sendToBot(req.params.name, { type: 'resume' });
    res.json({ ok: true, message: 'Resume command sent' });
  });

  router.post('/bots/:name/exit-trade', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;
    if (bot.status !== 'running') return res.status(400).json({ error: 'Bot not running' });

    await zmqBridge.sendToBot(req.params.name, { type: 'exit_trade' });
    res.json({ ok: true, message: 'Exit trade command sent' });
  });

  // ── Trade Data ────────────────────────────────────────────────────

  router.get('/bots/:name/trades', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;

    const tableCount = parseInt(req.query.count) || 100;
    const statsCount = Math.max(parseInt(req.query.statsCount) || 500, tableCount);

    try {
      const tradeLogPath = getTradeLogPath(bot.configPath, projectRoot);
      const diagnosticsPath = getDiagnosticsPath(bot.configPath, projectRoot);
      if (!tradeLogPath) {
        return res.json({ rawTrades: [], reportTrades: [], diagnostics: [] });
      }
      const chronological = await readFullTradeLogChronological(tradeLogPath);
      const rawDesc = [...chronological].slice(-statsCount).reverse();
      const reportChronological = buildTradeTableRows(chronological);
      const reportDesc = [...reportChronological].slice(-tableCount).reverse();
      let diagnostics = [];
      if (diagnosticsPath && existsSync(diagnosticsPath)) {
        diagnostics = await readDiagnostics(diagnosticsPath, statsCount);
      }
      res.json({ rawTrades: rawDesc, reportTrades: reportDesc, diagnostics });
    } catch (err) {
      if (err.code === 'ENOENT') {
        return res.json({ rawTrades: [], reportTrades: [], diagnostics: [] });
      }
      res.status(500).json({ error: err.message });
    }
  });

  // ── OHLCV Data ────────────────────────────────────────────────────

  router.get('/bots/:name/ohlcv', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;

    const range = req.query.range || '1M';

    try {
      const dataDir = getDataDir(bot.configPath, projectRoot);
      if (!dataDir) {
        return res.json([]);
      }
      const ohlcv = await readOHLCV(dataDir, bot.symbol, '5m', range);
      res.json(ohlcv);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Supertrend Overlay ─────────────────────────────────────────────

  router.get('/bots/:name/supertrend', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;

    const range = req.query.range || '1M';

    try {
      const dataDir = getDataDir(bot.configPath, projectRoot);
      if (!dataDir) return res.json([]);

      const { atrPeriod, multiplier, resampleInterval } = getSupertrendParams(bot.configPath, projectRoot);

      const warmupRange = addWarmupBuffer(range);

      const raw5m = await readRawOHLCV(dataDir, bot.symbol, '5m', warmupRange);
      if (raw5m.length === 0) return res.json([]);

      const ohlcvOutput = await readOHLCV(dataDir, bot.symbol, '5m', range);

      const stData = computeSupertrendFromRaw(raw5m, atrPeriod, multiplier, ohlcvOutput, resampleInterval);
      res.json(stData);
    } catch (err) {
      console.error(`[API] /supertrend error for ${req.params.name}:`, err.message, err.stack);
      res.status(500).json({ error: err.message });
    }
  });

  // ── SwingParty Chart Data ────────────────────────────────────────

  router.get('/bots/:name/swing-party-chart', async (req, res) => {
    const bot = getUserBot(req, res);
    if (!bot) return;

    try {
      const { assets, botDataDir, ohlcvDir, filePattern, stParams } = getSwingPartyAssets(bot.configPath, projectRoot);
      if (!assets || assets.length === 0) {
        return res.json({ error: 'No assets configured' });
      }

      const range = req.query.range || '1M';
      const rangeDays = { '1W': 7, '1M': 30, '3M': 90, '6M': 180, '1Y': 365 }[range] || 30;
      const cutoff = Date.now() - rangeDays * 86400_000;

      // Load OHLCV for each asset (prefers live data from bot data dir)
      const assetOhlcv = {};
      for (const sym of assets) {
        const rows = await readMultiAssetOHLCV(ohlcvDir, sym, filePattern, cutoff, botDataDir);
        if (rows.length > 0) assetOhlcv[sym] = rows;
      }

      if (Object.keys(assetOhlcv).length === 0) {
        return res.json({ error: 'No OHLCV data found' });
      }

      // Read trade log with raw position columns
      const tradeLogPath = getTradeLogPath(bot.configPath, projectRoot);
      const tradeRows = tradeLogPath ? await readTradeLogWithRaw(tradeLogPath, 10000) : [];

      const chartData = buildSwingPartyChartData(assetOhlcv, tradeRows, stParams);
      res.json(chartData);
    } catch (err) {
      console.error(`[API] /swing-party-chart error for ${req.params.name}:`, err.message, err.stack);
      res.status(500).json({ error: err.message });
    }
  });

  // ── Bot Logs ─────────────────────────────────────────────────────

  router.get('/bots/:name/logs', async (req, res) => {
    if (!requireAdmin(req, res)) return;
    const bot = getUserBot(req, res);
    if (!bot) return;

    const lines = parseInt(req.query.lines) || 200;
    const logPaths = getBotLogPaths(req.params.name, bot.configPath, projectRoot);

    const result = {};
    for (const [label, logPath] of Object.entries(logPaths)) {
      if (!logPath || !existsSync(logPath)) {
        result[label] = null;
        continue;
      }
      try {
        const content = await readFile(logPath, 'utf8');
        const allLines = content.split('\n');
        result[label] = allLines.slice(-lines).join('\n');
      } catch (err) {
        result[label] = `Error reading log: ${err.message}`;
      }
    }
    res.json(result);
  });

  router.get('/bots/:name/logs/download', async (req, res) => {
    if (!requireAdmin(req, res)) return;
    const bot = getUserBot(req, res);
    if (!bot) return;

    const source = req.query.source || 'process';
    const logPaths = getBotLogPaths(req.params.name, bot.configPath, projectRoot);
    const logPath = logPaths[source];

    if (!logPath || !existsSync(logPath)) {
      return res.status(404).json({ error: `Log file not found: ${source}` });
    }

    res.download(logPath, `${req.params.name}-${source}.log`);
  });

  return router;
}

function getBotLogPaths(botName, configPath, projectRoot) {
  const dashboardRoot = path.resolve(projectRoot, 'dashboard');
  const processLog = path.join(dashboardRoot, 'logs', `${botName}.log`);

  let pythonLog = null;
  try {
    const fullPath = path.resolve(projectRoot, configPath);
    const config = YAML.parse(readFileSync(fullPath, 'utf8'));
    const logFile = config?.logging?.file;
    if (logFile) pythonLog = path.resolve(projectRoot, logFile);
  } catch {}

  return { process: processLog, python: pythonLog };
}

function getTradeLogPath(configPath, projectRoot) {
  try {
    const fullPath = path.resolve(projectRoot, configPath);
    const config = YAML.parse(readFileSync(fullPath, 'utf8'));
    const tradelog = config?.reporting?.trade_log;
    if (tradelog) return path.resolve(projectRoot, tradelog);
    const dataDir = config?.bot?.data_dir || config?.paper_trading?.data_dir;
    if (dataDir) return path.resolve(projectRoot, dataDir, 'trades.csv');
    return null;
  } catch {
    return null;
  }
}

function getDataDir(configPath, projectRoot) {
  try {
    const fullPath = path.resolve(projectRoot, configPath);
    const config = YAML.parse(readFileSync(fullPath, 'utf8'));
    const dataDir = config?.bot?.data_dir || config?.paper_trading?.data_dir;
    if (dataDir) return path.resolve(projectRoot, dataDir);
    return null;
  } catch {
    return null;
  }
}

function getDiagnosticsPath(configPath, projectRoot) {
  const dataDir = getDataDir(configPath, projectRoot);
  if (!dataDir) return null;
  return path.resolve(dataDir, 'diagnostics.csv');
}

function resolveStrategyConfigPath(botYamlAbsPath, strategyConfigPath, projectRoot) {
  if (!strategyConfigPath) return null;
  if (path.isAbsolute(strategyConfigPath)) return strategyConfigPath;
  const botDir = path.dirname(botYamlAbsPath);
  const fromBotDir = path.resolve(botDir, strategyConfigPath);
  if (existsSync(fromBotDir)) return fromBotDir;
  const fromProjectRoot = path.resolve(projectRoot, strategyConfigPath);
  if (existsSync(fromProjectRoot)) return fromProjectRoot;
  return fromBotDir;
}

function getSupertrendParams(configPath, projectRoot) {
  try {
    const fullPath = path.resolve(projectRoot, configPath);
    const config = YAML.parse(readFileSync(fullPath, 'utf8'));
    const strategyConfigPath = config?.strategy?.config;
    if (!strategyConfigPath) return { atrPeriod: 20, multiplier: 2.5, resampleInterval: '1h' };

    const stratPath = resolveStrategyConfigPath(fullPath, strategyConfigPath, projectRoot);
    const stratConfig = YAML.parse(readFileSync(stratPath, 'utf8'));
    const stratEntry = stratConfig?.strategy || (stratConfig?.strategies || [])[0] || {};
    const params = stratEntry.params || {};

    return {
      atrPeriod: stratEntry.supertrend_atr_period ?? params.supertrend_atr_period ?? 20,
      multiplier: stratEntry.supertrend_multiplier ?? params.supertrend_multiplier ?? 2.5,
      resampleInterval: stratEntry.resample_interval ?? params.resample_interval ?? '1h',
    };
  } catch {
    return { atrPeriod: 20, multiplier: 2.5, resampleInterval: '1h' };
  }
}

function addWarmupBuffer(range) {
  const bufferDays = 30;
  const rangeDays = { '1W': 7, '1M': 30, '3M': 90, '6M': 180, '1Y': 365 }[range] || 30;
  const totalDays = rangeDays + bufferDays;
  if (totalDays <= 30) return '1M';
  if (totalDays <= 90) return '3M';
  if (totalDays <= 180) return '6M';
  return '1Y';
}

/**
 * Resolve SwingParty multi-asset config from bot YAML → strategy YAML.
 * Returns { assets, botDataDir, ohlcvDir, filePattern, stParams }.
 */
function getSwingPartyAssets(configPath, projectRoot) {
  try {
    const fullPath = path.resolve(projectRoot, configPath);
    const config = YAML.parse(readFileSync(fullPath, 'utf8'));

    const botDataDir = config?.bot?.data_dir
      ? path.resolve(projectRoot, config.bot.data_dir)
      : null;

    const stratConfigPath = config?.strategy?.config;
    if (!stratConfigPath) return { botDataDir };

    const stratPath = resolveStrategyConfigPath(fullPath, stratConfigPath, projectRoot);
    const stratConfig = YAML.parse(readFileSync(stratPath, 'utf8'));

    const stratEntry = stratConfig?.strategy || (stratConfig?.strategies || [])[0] || {};
    const assets = stratEntry.assets || stratEntry.params?.assets || [];
    const dsParams = stratConfig?.data_source?.params || {};
    const ohlcvDir = path.resolve(projectRoot, dsParams.data_dir || 'data/backtests');
    const filePattern = dsParams.file_pattern || '{symbol}-5m-2023-2026.csv';

    const stParams = {
      atrPeriod: stratEntry.supertrend_atr_period || stratEntry.params?.supertrend_atr_period || 10,
      multiplier: stratEntry.supertrend_multiplier || stratEntry.params?.supertrend_multiplier || 2.0,
      resampleInterval: stratEntry.resample_interval || stratEntry.params?.resample_interval || '1h',
    };

    return { assets, botDataDir, ohlcvDir, filePattern, stParams };
  } catch (err) {
    console.warn(`[API] getSwingPartyAssets failed for ${configPath}:`, err.message);
    return {};
  }
}

/**
 * Read OHLCV for one SwingParty asset: per-symbol subdir, then flat bot data_dir,
 * then combined backtest CSV under ohlcvDir.
 */
async function readMultiAssetOHLCV(ohlcvDir, symbol, filePattern, cutoffMs, botDataDir) {
  if (botDataDir) {
    // Per-symbol subdirs (default DataManager layout)
    const subdirRows = await _readMonthlySplitOHLCV(botDataDir, symbol, cutoffMs);
    if (subdirRows.length > 0) return subdirRows;
    // Flat dir: all `SYM-5m-YYYY-MM.csv` in bot data_dir (use_symbol_subdirs: false)
    const flatRows = await _readFlatMonthly5mInBotDir(botDataDir, symbol, cutoffMs);
    if (flatRows.length > 0) return flatRows;
  }

  // Fallback: single combined backtest CSV
  return _readCombinedOHLCV(ohlcvDir, symbol, filePattern, cutoffMs);
}

/**
 * Read monthly-split CSVs: {botDataDir}/{sym_lower}/{SYM}-5m-YYYY-MM.csv
 * Reads current + previous month to cover range.
 */
async function _readMonthlySplitOHLCV(botDataDir, symbol, cutoffMs) {
  const { createReadStream } = await import('node:fs');
  const csvParse = await import('csv-parse');
  const parse = csvParse.parse;

  const symDir = path.join(botDataDir, symbol.toLowerCase());
  if (!existsSync(symDir)) return [];

  // Find all matching 5m CSV files in the symbol directory
  const prefix = `${symbol}-5m-`;
  let files;
  try {
    files = readdirSync(symDir)
      .filter(f => f.startsWith(prefix) && f.endsWith('.csv'))
      .sort();
  } catch { return []; }

  if (files.length === 0) return [];

  const rows = [];
  for (const file of files) {
    const filePath = path.join(symDir, file);
    await new Promise((resolve, reject) => {
      const stream = createReadStream(filePath);
      stream.on('error', reject);
      stream
        .pipe(parse({ skip_empty_lines: true, relax_column_count: true }))
        .on('data', (cols) => {
          const ts = parseInt(cols[0]);
          if (isNaN(ts) || ts < cutoffMs) return;
          rows.push({
            timestamp: ts,
            open: parseFloat(cols[1]) || 0,
            high: parseFloat(cols[2]) || 0,
            low: parseFloat(cols[3]) || 0,
            close: parseFloat(cols[4]) || 0,
            volume: parseFloat(cols[5]) || 0,
          });
        })
        .on('end', resolve)
        .on('error', reject);
    });
  }

  return rows.sort((a, b) => a.timestamp - b.timestamp);
}

/**
 * Live bots with `use_symbol_subdirs: false`: `{botDataDir}/{SYM}-5m-YYYY-MM.csv`
 */
async function _readFlatMonthly5mInBotDir(botDataDir, symbol, cutoffMs) {
  if (!botDataDir || !existsSync(botDataDir)) return [];

  const { createReadStream } = await import('node:fs');
  const csvParse = await import('csv-parse');
  const parse = csvParse.parse;

  const prefix = `${symbol}-5m-`;
  let files;
  try {
    files = readdirSync(botDataDir)
      .filter((f) => f.startsWith(prefix) && f.endsWith('.csv'))
      .sort();
  } catch {
    return [];
  }

  if (files.length === 0) return [];

  const rows = [];
  for (const file of files) {
    const filePath = path.join(botDataDir, file);
    await new Promise((resolve, reject) => {
      const stream = createReadStream(filePath);
      stream.on('error', reject);
      stream
        .pipe(parse({ skip_empty_lines: true, relax_column_count: true }))
        .on('data', (cols) => {
          const ts = parseInt(cols[0]);
          if (isNaN(ts) || ts < cutoffMs) return;
          rows.push({
            timestamp: ts,
            open: parseFloat(cols[1]) || 0,
            high: parseFloat(cols[2]) || 0,
            low: parseFloat(cols[3]) || 0,
            close: parseFloat(cols[4]) || 0,
            volume: parseFloat(cols[5]) || 0,
          });
        })
        .on('end', resolve)
        .on('error', reject);
    });
  }

  return rows.sort((a, b) => a.timestamp - b.timestamp);
}

/** Read from a single combined backtest CSV. */
async function _readCombinedOHLCV(ohlcvDir, symbol, filePattern, cutoffMs) {
  const { createReadStream } = await import('node:fs');
  const csvParse = await import('csv-parse');
  const parse = csvParse.parse;

  const filename = filePattern.replace('{symbol}', symbol)
    .replace(/{start_year}/g, '').replace(/{end_year}/g, '')
    .replace(/--/g, '-');
  const filePath = path.join(ohlcvDir, filename);

  if (!existsSync(filePath)) return [];

  return new Promise((resolve, reject) => {
    const rows = [];
    const stream = createReadStream(filePath);
    stream.on('error', reject);
    stream
      .pipe(parse({ skip_empty_lines: true, relax_column_count: true }))
      .on('data', (cols) => {
        const ts = parseInt(cols[0]);
        if (isNaN(ts) || ts < cutoffMs) return;
        rows.push({
          timestamp: ts,
          open: parseFloat(cols[1]) || 0,
          high: parseFloat(cols[2]) || 0,
          low: parseFloat(cols[3]) || 0,
          close: parseFloat(cols[4]) || 0,
          volume: parseFloat(cols[5]) || 0,
        });
      })
      .on('end', () => resolve(rows.sort((a, b) => a.timestamp - b.timestamp)))
      .on('error', reject);
  });
}
