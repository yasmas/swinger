/**
 * REST API routes for the dashboard.
 * All routes require authentication (enforced by middleware in index.js).
 * Bot operations are scoped to the authenticated user.
 */

import { Router } from 'express';
import { readTradeLog, readOHLCV, readRawOHLCV } from '../csv-reader.js';
import { computeSupertrendFromRaw } from '../supertrend.js';
import path from 'path';
import { readFileSync, existsSync, statSync } from 'fs';
import { readFile } from 'fs/promises';
import YAML from 'yaml';

export function createApiRouter(botStateManager, zmqBridge, processManager, projectRoot) {
  const router = Router();

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

    const count = parseInt(req.query.count) || 100;

    try {
      const tradeLogPath = getTradeLogPath(bot.configPath, projectRoot);
      if (!tradeLogPath) {
        return res.json([]);
      }
      const trades = await readTradeLog(tradeLogPath, count);
      res.json(trades);
    } catch (err) {
      if (err.code === 'ENOENT') {
        return res.json([]);
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

  // ── Bot Logs ─────────────────────────────────────────────────────

  router.get('/bots/:name/logs', async (req, res) => {
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

function getSupertrendParams(configPath, projectRoot) {
  try {
    const fullPath = path.resolve(projectRoot, configPath);
    const config = YAML.parse(readFileSync(fullPath, 'utf8'));
    const strategyConfigPath = config?.strategy?.config;
    if (!strategyConfigPath) return { atrPeriod: 20, multiplier: 2.5 };

    const stratPath = path.resolve(projectRoot, strategyConfigPath);
    const stratConfig = YAML.parse(readFileSync(stratPath, 'utf8'));
    const params = stratConfig?.strategies?.[0]?.params || {};

    return {
      atrPeriod: params.supertrend_atr_period || 20,
      multiplier: params.supertrend_multiplier || 2.5,
      resampleInterval: params.resample_interval || '1h',
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
