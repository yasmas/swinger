/**
 * Dashboard server — Express + WebSocket + ZMQ.
 */

import express from 'express';
import cors from 'cors';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';
import { readFileSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import YAML from 'yaml';

import { BotStateManager } from './bot-state.js';
import { ZmqBridge } from './zmq-bridge.js';
import { ProcessManager } from './process-manager.js';
import { createApiRouter } from './routes/api.js';

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
const PORT = config.server?.port || 3000;
const ZMQ_PORT = config.server?.zmq_port || 5555;

// ── Initialize Components ───────────────────────────────────────────

const botStateManager = new BotStateManager();

// Register bots from config
for (const botDef of config.bots || []) {
  botStateManager.addBot(botDef.name, botDef);

  // Enrich with info from bot's YAML config
  try {
    const botConfigPath = path.resolve(PROJECT_ROOT, botDef.config_path);
    const botConfig = YAML.parse(readFileSync(botConfigPath, 'utf8'));
    botStateManager.enrichFromConfig(botDef.name, botConfig);
  } catch (err) {
    console.warn(`[Config] Could not read bot config for ${botDef.name}:`, err.message);
  }
}

// ── Express App ─────────────────────────────────────────────────────

const app = express();
app.use(cors());
app.use(express.json());

// Serve static React build
const clientDist = path.join(DASHBOARD_ROOT, 'client', 'dist');
app.use(express.static(clientDist));

// ── WebSocket ───────────────────────────────────────────────────────

const server = createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

const wsClients = new Set();

wss.on('connection', (ws) => {
  wsClients.add(ws);
  console.log(`[WS] Client connected (total: ${wsClients.size})`);

  // Send initial bot states
  ws.send(JSON.stringify({
    event: 'init',
    data: botStateManager.getAllBots(),
  }));

  ws.on('close', () => {
    wsClients.delete(ws);
    console.log(`[WS] Client disconnected (total: ${wsClients.size})`);
  });
});

function wsBroadcast(msg) {
  const data = JSON.stringify(msg);
  for (const ws of wsClients) {
    if (ws.readyState === 1) { // OPEN
      ws.send(data);
    }
  }
}

// ── ZMQ Bridge ──────────────────────────────────────────────────────

const zmqBridge = new ZmqBridge(ZMQ_PORT, botStateManager, wsBroadcast);

// ── Process Manager ─────────────────────────────────────────────────

const processManager = new ProcessManager(botStateManager, zmqBridge, PROJECT_ROOT);

// ── API Routes ──────────────────────────────────────────────────────

app.use('/api', createApiRouter(botStateManager, zmqBridge, processManager, PROJECT_ROOT));

// SPA fallback — serve index.html for any non-API route
app.get('*', (req, res) => {
  res.sendFile(path.join(clientDist, 'index.html'));
});

// ── Heartbeat Timeout Detection ─────────────────────────────────────

const HEARTBEAT_TIMEOUT_MS = 90_000; // 90 seconds

setInterval(() => {
  const now = Date.now();
  for (const bot of botStateManager.bots.values()) {
    if (bot.status === 'running' && bot.lastHeartbeat) {
      const elapsed = now - bot.lastHeartbeat.getTime();
      if (elapsed > HEARTBEAT_TIMEOUT_MS) {
        console.warn(`[Heartbeat] Bot ${bot.name} missed heartbeat (${Math.round(elapsed / 1000)}s ago)`);
        // Don't change status — the process manager tracks the actual process
        wsBroadcast({
          event: 'bot_update',
          bot: bot.name,
          data: { ...bot.toJSON(), heartbeatLate: true },
        });
      }
    }
  }
}, 30_000);

// ── Start ───────────────────────────────────────────────────────────

async function start() {
  await zmqBridge.start();

  server.listen(PORT, () => {
    console.log(`[Server] Dashboard running at http://localhost:${PORT}`);
    console.log(`[Server] API at http://localhost:${PORT}/api/bots`);
    console.log(`[Server] WebSocket at ws://localhost:${PORT}/ws`);
  });

  // Auto-start bots
  for (const botDef of config.bots || []) {
    if (botDef.auto_start) {
      console.log(`[AutoStart] Starting ${botDef.name}...`);
      try {
        await processManager.startBot(botDef.name);
      } catch (err) {
        console.error(`[AutoStart] Failed to start ${botDef.name}:`, err.message);
      }
    }
  }
}

// Graceful shutdown
async function shutdown() {
  console.log('\n[Server] Shutting down...');

  // Stop all running bots
  for (const bot of botStateManager.bots.values()) {
    if (bot.status === 'running' || bot.status === 'starting') {
      try {
        await processManager.stopBot(bot.name);
      } catch (err) {
        console.error(`[Shutdown] Error stopping ${bot.name}:`, err.message);
      }
    }
  }

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
