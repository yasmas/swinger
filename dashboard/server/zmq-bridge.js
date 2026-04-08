/**
 * ZMQ ROUTER socket — bridges Python bots to the Node dashboard.
 *
 * Each bot connects with a DEALER socket using identity = bot_name.
 * The ROUTER receives [identity, '', payload] frames.
 */

import { Router } from 'zeromq';

export class ZmqBridge {
  constructor(port, botStateManager, wsBroadcast) {
    this.port = port;
    this.botState = botStateManager;
    this.wsBroadcast = wsBroadcast;
    this.socket = null;
    this._running = false;

    this._pendingRequests = new Map();
    this._requestCounter = 0;
  }

  async start() {
    this.socket = new Router();
    await this.socket.bind(`tcp://*:${this.port}`);
    console.log(`[ZMQ] ROUTER bound on tcp://*:${this.port}`);
    this._running = true;
    this._receiveLoop();
  }

  async stop() {
    this._running = false;
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    for (const [, { timer }] of this._pendingRequests) {
      clearTimeout(timer);
    }
    this._pendingRequests.clear();
  }

  async _receiveLoop() {
    while (this._running && this.socket) {
      try {
        const frames = await this.socket.receive();
        const identity = frames[0].toString();
        const payload = JSON.parse(frames[frames.length - 1].toString());
        this._handleMessage(identity, payload);
      } catch (err) {
        if (this._running) {
          if (!err.message?.includes('closed')) {
            console.error('[ZMQ] Receive error:', err.message);
          }
        }
      }
    }
  }

  _handleMessage(botName, msg) {
    let bot = this.botState.getBot(botName);
    const msgType = msg.type;

    switch (msgType) {
      case 'hello':
        if (!bot) {
          console.warn(`[ZMQ] Ignoring hello from unknown bot: ${botName} (no owner registered)`);
          return;
        }
        {
          const wasDown = bot.status === 'stopped' || bot.status === 'crashed';
          bot.updateFromHello(msg);
          console.log(`[ZMQ] Bot ${wasDown ? 'reconnected' : 'connected'}: ${botName} (pid=${msg.pid})`);
          this.wsBroadcast({ event: 'bot_connected', bot: botName, data: bot.toJSON() }, bot.owner);
        }
        break;

      case 'status_update':
        if (!bot) {
          console.warn(`[ZMQ] Ignoring heartbeat from unknown bot: ${botName}`);
          return;
        }
        if (bot.status === 'stopped' || bot.status === 'crashed') {
          console.log(`[ZMQ] Bot ${botName} reconnected via heartbeat`);
          bot.status = 'running';
          this.wsBroadcast({ event: 'bot_connected', bot: botName, data: bot.toJSON() }, bot.owner);
        }
        bot.updateFromStatus(msg);
        this.wsBroadcast({ event: 'bot_update', bot: botName, data: bot.toJSON() }, bot.owner);
        break;

      case 'trade_entry':
        if (bot) {
          this.wsBroadcast({ event: 'trade_entry', bot: botName, data: msg }, bot.owner);
        }
        break;

      case 'trade_exit':
        if (bot) {
          this.wsBroadcast({ event: 'trade_exit', bot: botName, data: msg }, bot.owner);
        }
        break;

      case 'paused_ack':
        if (bot) {
          bot.paused = msg.paused;
          this.wsBroadcast({ event: 'bot_update', bot: botName, data: bot.toJSON() }, bot.owner);
        }
        break;

      case 'profile':
      case 'portfolio':
      case 'trades':
      case 'pnl_info':
      case 'price_data_path': {
        const requestId = msg.request_id;
        if (requestId && this._pendingRequests.has(requestId)) {
          const { resolve, timer } = this._pendingRequests.get(requestId);
          clearTimeout(timer);
          this._pendingRequests.delete(requestId);
          resolve(msg);
        }
        if (msgType === 'profile' && bot) bot.updateFromProfile(msg);
        if (msgType === 'portfolio' && bot) bot.updateFromStatus(msg);
        break;
      }

      default:
        console.warn(`[ZMQ] Unknown message type from ${botName}: ${msgType}`);
    }
  }

  async sendToBot(botName, msg) {
    if (!this.socket) return;
    try {
      await this.socket.send([botName, JSON.stringify(msg)]);
    } catch (err) {
      console.error(`[ZMQ] Failed to send to ${botName}:`, err.message);
    }
  }

  async requestInfo(botName, requests, params = {}, timeoutMs = 5000) {
    const requestId = `req_${++this._requestCounter}_${Date.now()}`;

    const promise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pendingRequests.delete(requestId);
        reject(new Error(`Request ${requestId} timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      this._pendingRequests.set(requestId, { resolve, timer });
    });

    await this.sendToBot(botName, {
      type: 'request_info',
      request_id: requestId,
      requests,
      params,
    });

    return promise;
  }
}
