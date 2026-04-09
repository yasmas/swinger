/**
 * Dashboard server — Express + WebSocket + ZMQ.
 * Multi-user: auth via config/users.yaml, per-user bot folders under data/<username>/.
 */

import express from 'express';
import cors from 'cors';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';
import { readFileSync, readdirSync, statSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import YAML from 'yaml';

// Prefix all console output with timestamps
const _origLog = console.log, _origWarn = console.warn, _origErr = console.error;
const _ts = () => new Date().toLocaleString('sv-SE', { hour12: false }).replace('T', ' ');
console.log = (...a) => _origLog(_ts(), ...a);
console.warn = (...a) => _origWarn(_ts(), ...a);
console.error = (...a) => _origErr(_ts(), ...a);

import { BotStateManager } from './bot-state.js';
import { ZmqBridge } from './zmq-bridge.js';
import { ProcessManager } from './process-manager.js';
import { createApiRouter } from './routes/api.js';
import { createAuthRouter } from './routes/auth.js';
import { SessionStore, loadUsersConfig } from './auth.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DASHBOARD_ROOT = path.resolve(__dirname, '..');
const PROJECT_ROOT = path.resolve(DASHBOARD_ROOT, '..');

// ── Load Config ─────────────────────────────────────────────────────

function loadConfig() {
  const configPath = path.join(DASHBOARD_ROOT, 'dashboard.yaml');
  try {
    return YAML.parse(readFileSync(configPath, 'utf8'));
  } catch (err) {
    console.error('Failed to load dashboard.yaml:', err.message);
    process.exit(1);
  }
}

const config = loadConfig();
const PORT = parseInt(process.env.DASHBOARD_PORT) || config.server?.port || 3000;
const ZMQ_PORT = parseInt(process.env.DASHBOARD_ZMQ_PORT) || config.server?.zmq_port || 5555;
const USERS_CONFIG_PATH = path.resolve(PROJECT_ROOT, process.env.DASHBOARD_USERS_CONFIG || config.server?.users_config || 'config/users.yaml');
const DATA_ROOT = path.join(PROJECT_ROOT, 'data');

// Strategy type → display name (mirrors Python strategies.registry)
const STRATEGY_DISPLAY_NAMES = {
  lazy_swing: 'LazySwing',
  swing_party: 'SwingParty',
  swing_trend: 'Swing Trend',
  intraday_trend: 'Intraday Trend',
  macd_rsi_advanced: 'MACD RSI Advanced',
  ma_crossover_rsi: 'MA Crossover RSI',
  buy_and_hold: 'Buy & Hold',
};

// ── Session Store ───────────────────────────────────────────────────

let usersConfig;
try {
  usersConfig = loadUsersConfig(USERS_CONFIG_PATH);
} catch (err) {
  console.error('Failed to load users config:', err.message);
  process.exit(1);
}

const sessionStore = new SessionStore(usersConfig.session_ttl_hours || 24);

// Periodic cleanup of expired sessions
setInterval(() => sessionStore.cleanup(), 60_000);

// ── Initialize Components ───────────────────────────────────────────

const botStateManager = new BotStateManager();

/**
 * Scan a user's data folder for bot config YAML files and register them.
 * Bot name = trader_name from the YAML (single source of truth).
 */
function scanUserBots(username) {
  const userDir = path.join(DATA_ROOT, username);
  let entries;
  try {
    entries = readdirSync(userDir);
  } catch {
    return;
  }

  for (const entry of entries) {
    if (!entry.endsWith('.yaml') && !entry.endsWith('.yml')) continue;
    const fullPath = path.join(userDir, entry);
    try {
      if (!statSync(fullPath).isFile()) continue;
    } catch { continue; }

    try {
      const botConfig = YAML.parse(readFileSync(fullPath, 'utf8'));

      // Skip YAML files that aren't bot configs (e.g. state.yaml)
      if (!botConfig || (!botConfig.trader_name && !botConfig.bot && !botConfig.broker)) continue;

      const traderName = botConfig.trader_name || path.basename(entry, path.extname(entry));
      const qualifiedName = `${username}:${traderName}`;

      if (botStateManager.getBot(qualifiedName)) continue;

      const relConfigPath = path.relative(PROJECT_ROOT, fullPath);
      const botDef = {
        owner: username,
        config_path: relConfigPath,
        auto_start: botConfig.auto_start || false,
        type: botConfig.broker?.type || 'paper',
      };

      const bot = botStateManager.addBot(qualifiedName, botDef);
      bot.displayName = traderName;

      // Enrich with strategy/exchange/symbol info
      enrichBot(qualifiedName, botConfig);

      console.log(`[Scan] Registered bot "${traderName}" for user ${username}`);
    } catch (err) {
      console.warn(`[Scan] Failed to parse ${fullPath}:`, err.message);
    }
  }
}

function enrichBot(botName, botConfig) {
  if (botConfig.strategy && botConfig.strategy.config) {
    try {
      const stratPath = path.resolve(PROJECT_ROOT, botConfig.strategy.config);
      const stratFile = YAML.parse(readFileSync(stratPath, 'utf8'));
      // Support both `strategies[0]` (LazySwing) and top-level `strategy` (SwingParty)
      const stratEntry = (stratFile.strategies || [])[0] || stratFile.strategy || {};
      const stratType = stratEntry.type || '';
      botConfig.strategy = {
        type: stratType,
        version: (stratFile.backtest || {}).version || '',
        display_name: STRATEGY_DISPLAY_NAMES[stratType] || stratType,
        params: stratEntry.params || stratEntry,
      };
    } catch (stratErr) {
      console.warn(`[Config] Could not resolve strategy config for ${botName}:`, stratErr.message);
    }
  }
  botStateManager.enrichFromConfig(botName, botConfig);
}

// Scan only folders belonging to known users from users.yaml
function scanAllUsers() {
  for (const user of usersConfig.users || []) {
    const username = user.email.split('@')[0].toLowerCase();
    scanUserBots(username);
  }
}

scanAllUsers();

// ── Express App ─────────────────────────────────────────────────────

const app = express();
app.use(cors());
app.use(express.json());

// Serve static React build
const clientDist = path.join(DASHBOARD_ROOT, 'client', 'dist');
app.use(express.static(clientDist));

// ── Auth Middleware ──────────────────────────────────────────────────

app.use('/api/auth', createAuthRouter(sessionStore, USERS_CONFIG_PATH, DATA_ROOT, (username) => {
  scanUserBots(username);
}));

function authMiddleware(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader?.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Authentication required' });
  }
  const session = sessionStore.validate(authHeader.slice(7));
  if (!session) {
    return res.status(401).json({ error: 'Session expired' });
  }
  req.user = session;
  next();
}

app.use('/api', authMiddleware);

// ── WebSocket ───────────────────────────────────────────────────────

const server = createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

/** @type {Map<WebSocket, {username: string, email: string}>} */
const wsClients = new Map();

wss.on('connection', (ws, req) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const token = url.searchParams.get('token');
  const session = token ? sessionStore.validate(token) : null;

  if (!session) {
    ws.close(4001, 'Authentication required');
    return;
  }

  wsClients.set(ws, session);
  console.log(`[WS] Client connected: ${session.username} (total: ${wsClients.size})`);

  // Send initial bot states for this user
  ws.send(JSON.stringify({
    event: 'init',
    data: botStateManager.getBotsForUser(session.username),
  }));

  ws.on('close', () => {
    wsClients.delete(ws);
    console.log(`[WS] Client disconnected (total: ${wsClients.size})`);
  });
});

/**
 * Broadcast a message to WebSocket clients.
 * If owner is specified, only send to clients matching that username.
 */
function wsBroadcast(msg, owner) {
  const data = JSON.stringify(msg);
  for (const [ws, session] of wsClients) {
    if (ws.readyState === 1) {
      if (!owner || session.username === owner) {
        ws.send(data);
      }
    }
  }
}

// ── ZMQ Bridge ──────────────────────────────────────────────────────

const zmqBridge = new ZmqBridge(ZMQ_PORT, botStateManager, wsBroadcast);

// ── Process Manager ─────────────────────────────────────────────────

const processManager = new ProcessManager(botStateManager, zmqBridge, PROJECT_ROOT, wsBroadcast);

// ── API Routes ──────────────────────────────────────────────────────

app.use('/api', createApiRouter(botStateManager, zmqBridge, processManager, PROJECT_ROOT));

// SPA fallback — serve index.html for any non-API route
app.get('*', (req, res) => {
  res.sendFile(path.join(clientDist, 'index.html'));
});

// ── Heartbeat Timeout Detection ─────────────────────────────────────

// Allow enough time for API retries during network outages (3 retries × 30s timeout each).
// Bot sends heartbeats every 5s, but they block on API calls for portfolio state.
const HEARTBEAT_TIMEOUT_MS = 120_000;

setInterval(() => {
  const now = Date.now();
  for (const bot of botStateManager.bots.values()) {
    if (bot.status === 'running' && bot.lastHeartbeat) {
      const elapsed = now - bot.lastHeartbeat.getTime();
      if (elapsed > HEARTBEAT_TIMEOUT_MS) {
        console.warn(`[Heartbeat] Bot ${bot.name} missed heartbeat (${Math.round(elapsed / 1000)}s ago) — marking crashed`);
        bot.status = 'crashed';
        bot.process = null;
        bot.pid = null;
        wsBroadcast({ event: 'bot_update', bot: bot.name, data: bot.toJSON() }, bot.owner);
      }
    }
  }
}, 5_000);

// ── Start ───────────────────────────────────────────────────────────

async function start() {
  await zmqBridge.start();

  server.listen(PORT, () => {
    console.log(`[Server] Dashboard running at http://localhost:${PORT}`);
    console.log(`[Server] API at http://localhost:${PORT}/api/bots`);
    console.log(`[Server] WebSocket at ws://localhost:${PORT}/ws`);
    console.log(`[Server] Users config: ${USERS_CONFIG_PATH}`);
  });

  // Auto-start bots that have auto_start: true in their config
  for (const bot of botStateManager.bots.values()) {
    if (bot.autoStart) {
      console.log(`[AutoStart] Starting ${bot.name}...`);
      try {
        await processManager.startBot(bot.name);
      } catch (err) {
        console.error(`[AutoStart] Failed to start ${bot.name}:`, err.message);
      }
    }
  }
}

// Graceful shutdown — bots keep running, they'll reconnect when dashboard restarts
async function shutdown() {
  console.log('\n[Server] Shutting down (bots will keep running)...');
  await zmqBridge.stop();
  server.close();
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

start().catch(err => {
  console.error('[Server] Failed to start:', err);
  process.exit(1);
});
