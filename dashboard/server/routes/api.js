/**
 * REST API routes for the dashboard.
 */

import { Router } from 'express';
import { readTradeLog, readOHLCV } from '../csv-reader.js';
import path from 'path';
import { readFileSync, existsSync, statSync } from 'fs';
import { readFile } from 'fs/promises';
import YAML from 'yaml';

export function createApiRouter(botStateManager, zmqBridge, processManager, projectRoot) {
  const router = Router();

  // ── Bot List ──────────────────────────────────────────────────────

  router.get('/bots', (req, res) => {
    res.json(botStateManager.getAllBots());
  });

  router.get('/bots/:name', (req, res) => {
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });
    res.json(bot.toJSON());
  });

  // ── Bot Control ───────────────────────────────────────────────────

  router.post('/bots/:name/start', async (req, res) => {
    try {
      const result = await processManager.startBot(req.params.name);
      res.json(result);
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  router.post('/bots/:name/stop', async (req, res) => {
    try {
      const result = await processManager.stopBot(req.params.name);
      res.json(result);
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  router.post('/bots/:name/pause', async (req, res) => {
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });
    if (bot.status !== 'running') return res.status(400).json({ error: 'Bot not running' });

    await zmqBridge.sendToBot(req.params.name, { type: 'pause' });
    res.json({ ok: true, message: 'Pause command sent' });
  });

  router.post('/bots/:name/resume', async (req, res) => {
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });
    if (bot.status !== 'running') return res.status(400).json({ error: 'Bot not running' });

    await zmqBridge.sendToBot(req.params.name, { type: 'resume' });
    res.json({ ok: true, message: 'Resume command sent' });
  });

  router.post('/bots/:name/exit-trade', async (req, res) => {
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });
    if (bot.status !== 'running') return res.status(400).json({ error: 'Bot not running' });

    await zmqBridge.sendToBot(req.params.name, { type: 'exit_trade' });
    res.json({ ok: true, message: 'Exit trade command sent' });
  });

  // ── Trade Data ────────────────────────────────────────────────────

  router.get('/bots/:name/trades', async (req, res) => {
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });

    const count = parseInt(req.query.count) || 100;

    // Try to read trade log from the bot's config
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
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });

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

  // ── Bot Logs ─────────────────────────────────────────────────────

  router.get('/bots/:name/logs', async (req, res) => {
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });

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
    const bot = botStateManager.getBot(req.params.name);
    if (!bot) return res.status(404).json({ error: 'Bot not found' });

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

/**
 * Get log file paths for a bot.
 * - process: dashboard/logs/{name}.log (stdout+stderr from spawned process)
 * - python: the logging.file from the bot's config YAML
 */
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

/**
 * Extract the trade log path from a bot's config YAML.
 */
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

/**
 * Extract the data directory from a bot's config YAML.
 */
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
