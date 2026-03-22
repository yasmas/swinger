/**
 * REST API routes for the dashboard.
 */

import { Router } from 'express';
import { readTradeLog, readOHLCV } from '../csv-reader.js';
import path from 'path';
import { readFileSync } from 'fs';
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

  return router;
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
    const dataDir = config?.paper_trading?.data_dir;
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
    const dataDir = config?.paper_trading?.data_dir;
    if (dataDir) return path.resolve(projectRoot, dataDir);
    return null;
  } catch {
    return null;
  }
}
