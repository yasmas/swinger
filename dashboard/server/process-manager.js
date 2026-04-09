/**
 * Process manager — spawn/stop Python bot processes.
 * Supports stopping reconnected bots (no child process handle, kill by PID).
 */

import { spawn } from 'child_process';
import { createWriteStream, existsSync, readFileSync, writeFileSync } from 'fs';
import { mkdir } from 'fs/promises';
import path from 'path';
import YAML from 'yaml';

export class ProcessManager {
  constructor(botStateManager, zmqBridge, projectRoot, wsBroadcast) {
    this.botState = botStateManager;
    this.zmq = zmqBridge;
    this.projectRoot = projectRoot;
    this.wsBroadcast = wsBroadcast;
    this.logDir = path.join(projectRoot, 'dashboard', 'logs');
  }

  /**
   * Ensure the bot config's data paths point inside the user's data folder.
   * Rewrites data_dir, state_file, trade_log, and logging.file in-place
   * on the YAML file so the Python bot writes to the correct location.
   */
  _ensureUserDataPaths(bot) {
    const configPath = path.resolve(this.projectRoot, bot.configPath);
    const raw = readFileSync(configPath, 'utf8');
    const config = YAML.parse(raw);

    const userDir = path.dirname(configPath);
    const botSlug = path.basename(bot.configPath, '.yaml');
    const botDataDir = path.join(userDir, botSlug);

    const relDataDir = path.relative(this.projectRoot, botDataDir);

    let changed = false;
    const botCfg = config.bot || config.paper_trading || {};
    const section = config.bot ? 'bot' : 'paper_trading';

    if (!config[section]) config[section] = botCfg;

    if (botCfg.data_dir !== relDataDir) {
      botCfg.data_dir = relDataDir;
      changed = true;
    }
    const expectedStateFile = path.join(relDataDir, 'state.yaml');
    if (botCfg.state_file !== expectedStateFile) {
      botCfg.state_file = expectedStateFile;
      changed = true;
    }

    if (!config.reporting) config.reporting = {};
    const expectedTradeLog = path.join(relDataDir, 'trades.csv');
    if (config.reporting.trade_log !== expectedTradeLog) {
      config.reporting.trade_log = expectedTradeLog;
      changed = true;
    }

    if (!config.logging) config.logging = {};
    const expectedLogFile = path.join(relDataDir, 'swing_bot.log');
    if (config.logging.file !== expectedLogFile) {
      config.logging.file = expectedLogFile;
      changed = true;
    }

    if (changed) {
      const doc = new YAML.Document(config);
      writeFileSync(configPath, doc.toString(), 'utf8');
      console.log(`[PM] Rewrote data paths in ${bot.configPath} → ${relDataDir}`);
    }

    return botDataDir;
  }

  async startBot(botName) {
    const bot = this.botState.getBot(botName);
    if (!bot) throw new Error(`Unknown bot: ${botName}`);
    if (bot.status === 'running' || bot.status === 'starting') {
      throw new Error(`Bot ${botName} is already ${bot.status}`);
    }

    const botDataDir = this._ensureUserDataPaths(bot);
    await mkdir(botDataDir, { recursive: true });

    const configPath = path.resolve(this.projectRoot, bot.configPath);
    bot.status = 'starting';

    await mkdir(this.logDir, { recursive: true });

    const logFile = path.join(this.logDir, `${botName}.log`);
    const logStream = createWriteStream(logFile, { flags: 'a' });

    const timestamp = new Date().toISOString();
    logStream.write(`\n${'='.repeat(60)}\n[${timestamp}] Starting ${botName}\n${'='.repeat(60)}\n`);

    const env = {
      ...process.env,
      PYTHONPATH: path.join(this.projectRoot, 'src'),
    };

    const venvPython = path.join(this.projectRoot, '.venv', 'bin', 'python3');
    const pythonBin = existsSync(venvPython) ? venvPython : 'python3';

    const child = spawn(
      pythonBin,
      [path.join(this.projectRoot, 'src', 'trading', 'swing_bot.py'), configPath, bot.owner],
      {
        cwd: this.projectRoot,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: true,
      }
    );

    child.stdout.pipe(logStream);
    child.stderr.pipe(logStream);

    bot.process = child;
    bot.pid = child.pid;
    bot.exitCode = null;

    child.on('exit', (code, signal) => {
      const wasRunning = bot.status === 'running' || bot.status === 'starting';
      bot.process = null;

      if (bot.status === 'stopping') {
        bot.status = 'stopped';
        bot.exitCode = code;
        console.log(`[PM] Bot ${botName} stopped (code=${code}, signal=${signal})`);
      } else if (wasRunning) {
        bot.status = 'crashed';
        bot.exitCode = code;
        console.error(`[PM] Bot ${botName} crashed (code=${code}, signal=${signal})`);
      }

      this.wsBroadcast({ event: 'bot_update', bot: botName, data: bot.toJSON() }, bot.owner);
      logStream.end(`\n[${new Date().toISOString()}] Process exited: code=${code} signal=${signal}\n`);
    });

    child.on('error', (err) => {
      bot.status = 'crashed';
      bot.process = null;
      console.error(`[PM] Failed to start ${botName}:`, err.message);
      this.wsBroadcast({ event: 'bot_update', bot: botName, data: bot.toJSON() }, bot.owner);
      logStream.end(`\n[${new Date().toISOString()}] Spawn error: ${err.message}\n`);
    });

    await new Promise(resolve => setTimeout(resolve, 500));
    if (bot.status === 'starting' && bot.process) {
      bot.status = 'running';
    }

    console.log(`[PM] Started ${botName} (pid=${child.pid})`);
    return bot.toJSON();
  }

  async stopBot(botName) {
    const bot = this.botState.getBot(botName);
    if (!bot) throw new Error(`Unknown bot: ${botName}`);
    if (bot.status === 'stopped') {
      throw new Error(`Bot ${botName} is already stopped`);
    }
    // Allow stopping crashed bots (resets their status so they can be restarted)
    if (bot.status === 'crashed') {
      bot.status = 'stopped';
      bot.pid = null;
      bot.process = null;
      console.log(`[PM] Cleared crashed bot ${botName}`);
      this.wsBroadcast({ event: 'bot_update', bot: botName, data: bot.toJSON() }, bot.owner);
      return bot.toJSON();
    }

    bot.status = 'stopping';

    try {
      await this.zmq.sendToBot(botName, { type: 'quit' });
    } catch (err) {
      console.warn(`[PM] Failed to send quit to ${botName}:`, err.message);
    }

    const child = bot.process;
    if (child) {
      const exited = await this._waitForExit(child, 5000);
      if (!exited) {
        console.log(`[PM] Sending SIGTERM to ${botName} (pid=${child.pid})`);
        child.kill('SIGTERM');
        const exitedAfterTerm = await this._waitForExit(child, 3000);
        if (!exitedAfterTerm) {
          console.log(`[PM] Sending SIGKILL to ${botName} (pid=${child.pid})`);
          child.kill('SIGKILL');
        }
      }
    } else if (bot.pid) {
      if (!this._isProcessAlive(bot.pid)) {
        console.log(`[PM] Bot ${botName} (pid=${bot.pid}) already gone`);
      } else {
        await this._waitForPidExit(bot.pid, 5000);
        if (this._isProcessAlive(bot.pid)) {
          console.log(`[PM] Sending SIGTERM to reconnected bot ${botName} (pid=${bot.pid})`);
          try { process.kill(bot.pid, 'SIGTERM'); } catch {}
          await this._waitForPidExit(bot.pid, 3000);
          if (this._isProcessAlive(bot.pid)) {
            console.log(`[PM] Sending SIGKILL to reconnected bot ${botName} (pid=${bot.pid})`);
            try { process.kill(bot.pid, 'SIGKILL'); } catch {}
          }
        }
      }
    }

    if (bot.status === 'stopping') {
      bot.status = 'stopped';
    }
    bot.pid = null;

    console.log(`[PM] Stopped ${botName}`);
    return bot.toJSON();
  }

  _isProcessAlive(pid) {
    try {
      process.kill(pid, 0);
      return true;
    } catch {
      return false;
    }
  }

  _waitForPidExit(pid, timeoutMs) {
    return new Promise(resolve => {
      const start = Date.now();
      const check = () => {
        if (!this._isProcessAlive(pid)) {
          resolve(true);
        } else if (Date.now() - start >= timeoutMs) {
          resolve(false);
        } else {
          setTimeout(check, 200);
        }
      };
      check();
    });
  }

  _waitForExit(child, timeoutMs) {
    return new Promise(resolve => {
      if (child.exitCode !== null || child.killed) {
        resolve(true);
        return;
      }
      const timer = setTimeout(() => {
        child.removeListener('exit', onExit);
        resolve(false);
      }, timeoutMs);

      function onExit() {
        clearTimeout(timer);
        resolve(true);
      }
      child.once('exit', onExit);
    });
  }
}
