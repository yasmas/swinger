/**
 * Process manager — spawn/stop Python bot processes.
 * Supports stopping reconnected bots (no child process handle, kill by PID).
 */

import { spawn } from 'child_process';
import { createWriteStream, existsSync } from 'fs';
import { mkdir } from 'fs/promises';
import path from 'path';

export class ProcessManager {
  constructor(botStateManager, zmqBridge, projectRoot) {
    this.botState = botStateManager;
    this.zmq = zmqBridge;
    this.projectRoot = projectRoot;
    this.logDir = path.join(projectRoot, 'dashboard', 'logs');
  }

  async startBot(botName) {
    const bot = this.botState.getBot(botName);
    if (!bot) throw new Error(`Unknown bot: ${botName}`);
    if (bot.status === 'running' || bot.status === 'starting') {
      throw new Error(`Bot ${botName} is already ${bot.status}`);
    }

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

    // Use venv Python if available, otherwise fall back to system python3
    const venvPython = path.join(this.projectRoot, '.venv', 'bin', 'python3');
    const pythonBin = existsSync(venvPython) ? venvPython : 'python3';

    const child = spawn(
      pythonBin,
      [path.join(this.projectRoot, 'src', 'paper_trading', 'paper_trader.py'), configPath],
      {
        cwd: this.projectRoot,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
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

      logStream.end(`\n[${new Date().toISOString()}] Process exited: code=${code} signal=${signal}\n`);
    });

    child.on('error', (err) => {
      bot.status = 'crashed';
      bot.process = null;
      console.error(`[PM] Failed to start ${botName}:`, err.message);
      logStream.end(`\n[${new Date().toISOString()}] Spawn error: ${err.message}\n`);
    });

    // Give the process a moment to start — if it crashes immediately, we'll know
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
    if (bot.status !== 'running' && bot.status !== 'starting') {
      throw new Error(`Bot ${botName} is not running (status=${bot.status})`);
    }

    bot.status = 'stopping';

    // 1. Send ZMQ quit command
    try {
      await this.zmq.sendToBot(botName, { type: 'quit' });
    } catch (err) {
      console.warn(`[PM] Failed to send quit to ${botName}:`, err.message);
    }

    // 2. Wait for graceful shutdown
    const child = bot.process;
    if (child) {
      // We spawned this bot — use child process handle
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
      // Reconnected bot — no child process handle, kill by PID
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
