/**
 * In-memory state for each bot. Updated by ZMQ messages from bots.
 */

export class BotState {
  constructor(name, configDef) {
    this.name = name;
    this.configPath = configDef.config_path || '';
    this.type = configDef.type || 'paper';
    this.autoStart = configDef.auto_start || false;

    // Process state
    this.status = 'stopped'; // stopped | starting | running | crashed | stopping
    this.pid = null;
    this.process = null;
    this.startedAt = null;
    this.exitCode = null;

    // Profile (from hello/profile messages)
    this.strategy = configDef.strategy || '';
    this.version = configDef.version || '';
    this.exchange = configDef.exchange || '';
    this.symbol = configDef.symbol || '';
    this.initialCash = 0;

    // Live state (from status_update)
    this.portfolioValue = 0;
    this.cash = 0;
    this.position = 'FLAT';
    this.positionQty = 0;
    this.positionAvgCost = 0;
    this.lastPrice = 0;
    this.paused = false;
    this.lastHeartbeat = null;

    // Cached data
    this.recentTrades = [];
  }

  updateFromHello(msg) {
    this.pid = msg.pid;
    this.startedAt = msg.started_at;
    this.strategy = msg.strategy || this.strategy;
    this.version = msg.version || this.version;
    this.displayName = msg.display_name || this.displayName;
    this.exchange = msg.exchange || this.exchange;
    this.symbol = msg.symbol || this.symbol;
    this.status = 'running';
  }

  updateFromStatus(msg) {
    this.portfolioValue = msg.portfolio_value ?? this.portfolioValue;
    this.cash = msg.cash ?? this.cash;
    this.position = msg.position ?? this.position;
    this.positionQty = msg.position_qty ?? this.positionQty;
    this.positionAvgCost = msg.position_avg_cost ?? this.positionAvgCost;
    this.lastPrice = msg.last_price ?? this.lastPrice;
    this.paused = msg.paused ?? this.paused;
    this.lastHeartbeat = new Date();
  }

  updateFromProfile(msg) {
    this.strategy = msg.strategy || this.strategy;
    this.version = msg.version || this.version;
    this.displayName = msg.display_name || this.displayName;
    this.exchange = msg.exchange || this.exchange;
    this.symbol = msg.symbol || this.symbol;
    this.initialCash = msg.initial_cash ?? this.initialCash;
    this.pid = msg.pid ?? this.pid;
  }

  toJSON() {
    return {
      name: this.name,
      displayName: this.displayName || '',
      configPath: this.configPath,
      type: this.type,
      status: this.status,
      pid: this.pid,
      startedAt: this.startedAt,
      strategy: this.strategy,
      version: this.version,
      exchange: this.exchange,
      symbol: this.symbol,
      initialCash: this.initialCash,
      portfolioValue: this.portfolioValue,
      cash: this.cash,
      position: this.position,
      positionQty: this.positionQty,
      positionAvgCost: this.positionAvgCost,
      lastPrice: this.lastPrice,
      paused: this.paused,
      lastHeartbeat: this.lastHeartbeat,
      brokerType: this.brokerType || 'paper',
    };
  }
}

export class BotStateManager {
  constructor() {
    /** @type {Map<string, BotState>} */
    this.bots = new Map();
  }

  addBot(name, configDef) {
    const state = new BotState(name, configDef);
    this.bots.set(name, state);
    return state;
  }

  getBot(name) {
    return this.bots.get(name);
  }

  getAllBots() {
    return Array.from(this.bots.values()).map(b => b.toJSON());
  }

  /**
   * Try to extract bot display info from its YAML config file.
   */
  enrichFromConfig(name, config) {
    const bot = this.bots.get(name);
    if (!bot || !config) return;

    const strat = config.strategy || {};
    const botCfg = config.bot || config.paper_trading || {};
    const brokerCfg = config.broker || {};
    const ex = config.exchange || {};

    bot.strategy = bot.strategy || strat.type || '';
    bot.version = bot.version || strat.version || '';
    bot.displayName = bot.displayName || strat.display_name || '';
    bot.symbol = bot.symbol || botCfg.symbol || '';
    bot.exchange = bot.exchange || ex.type || '';
    bot.initialCash = botCfg.initial_cash || brokerCfg.initial_cash || 0;
    bot.brokerType = brokerCfg.type || 'paper';
  }
}
