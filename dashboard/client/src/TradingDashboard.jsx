import { useState, useEffect, useMemo, useCallback } from "react";
import { XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, AreaChart, Area, ComposedChart, Bar, Line } from "recharts";

import { computePnlStats } from "./lib/pnl.js";
import PriceChart from "./PriceChart.jsx";

// ── Utility Components ─────────────────────────────────────────────────
const PnlBadge = ({ value, suffix = "%" }) => {
  const color = value > 0 ? "#22c55e" : value < 0 ? "#ef4444" : "#94a3b8";
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : "–";
  return <span style={{ color, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{arrow} {Math.abs(value).toFixed(1)}{suffix}</span>;
};

const StatusDot = ({ status }) => {
  const isRunning = status === "running";
  const color = isRunning ? "#22c55e" : status === "crashed" ? "#ef4444" : "#94a3b8";
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, boxShadow: isRunning ? "0 0 6px #22c55e88" : "none" }} />
      <span style={{ fontWeight: 500, color }}>{label}</span>
    </span>
  );
};

const PositionBadge = ({ position }) => {
  const colors = { LONG: { bg: "#22c55e20", color: "#22c55e", border: "#22c55e40" }, SHORT: { bg: "#ef444420", color: "#ef4444", border: "#ef444440" }, FLAT: { bg: "#94a3b820", color: "#94a3b8", border: "#94a3b840" } };
  const c = colors[position] || colors.FLAT;
  return <span style={{ background: c.bg, color: c.color, border: `1px solid ${c.border}`, borderRadius: 4, padding: "2px 10px", fontSize: 12, fontWeight: 700, letterSpacing: 1 }}>{position}</span>;
};

// ── Main Dashboard ─────────────────────────────────────────────────────
export default function TradingDashboard({ bots, setBots, tradeTick = 0 }) {
  const [activeTab, setActiveTab] = useState(0);
  const [trades, setTrades] = useState([]);
  const [ohlcv, setOhlcv] = useState([]);
  const [chartRange, setChartRange] = useState("1M");
  const [actionLoading, setActionLoading] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [logContent, setLogContent] = useState(null);
  const [logSource, setLogSource] = useState("process");

  const bot = bots[activeTab] || null;
  const isRunning = bot?.status === "running";

  // Fetch trades when bot changes
  useEffect(() => {
    if (!bot) return;
    fetch(`/api/bots/${bot.name}/trades?count=200`)
      .then(r => r.json())
      .then(setTrades)
      .catch(err => console.error("Failed to fetch trades:", err));
  }, [bot?.name, bot?.status, tradeTick]);

  // Fetch OHLCV when bot or range changes, and auto-refresh every 5 minutes
  const [ohlcvTick, setOhlcvTick] = useState(0);
  useEffect(() => {
    if (!bot) return;
    fetch(`/api/bots/${bot.name}/ohlcv?range=${chartRange}`)
      .then(r => r.json())
      .then(setOhlcv)
      .catch(err => console.error("Failed to fetch OHLCV:", err));
  }, [bot?.name, chartRange, ohlcvTick]);

  // Auto-refresh OHLCV every 5 minutes
  useEffect(() => {
    if (!bot || !isRunning) return;
    const timer = setInterval(() => setOhlcvTick(t => t + 1), 5 * 60 * 1000);
    return () => clearInterval(timer);
  }, [bot?.name, isRunning]);

  // Compute PnL stats from trades
  const pnlStats = useMemo(() => {
    return computePnlStats(trades, bot?.initialCash || 100000);
  }, [trades, bot?.initialCash]);

  // Bot control actions
  const botAction = useCallback(async (action) => {
    if (!bot) return;
    setActionLoading(true);
    try {
      const res = await fetch(`/api/bots/${bot.name}/${action}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        console.error(`Action ${action} failed:`, data.error);
      } else if (data.name) {
        // Update local state immediately from response
        setBots(prev => prev.map(b => b.name === data.name ? { ...b, ...data } : b));
      }
    } catch (err) {
      console.error(`Action ${action} failed:`, err);
    }
    setActionLoading(false);
  }, [bot, setBots]);

  // CSV download
  const downloadCSV = useCallback(() => {
    const header = "Date,Action,Symbol,Qty,Price,Cash Balance,Portfolio Value\n";
    const csv = header + trades.map(t =>
      `${t.date},${t.action},${t.symbol},${t.qty},${t.price},${t.cashBalance},${t.portfolioValue}`
    ).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${bot?.name || "trades"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [trades, bot]);

  // Fetch logs
  const fetchLogs = useCallback(async () => {
    if (!bot) return;
    setShowLogs(true);
    setLogContent(null);
    try {
      const res = await fetch(`/api/bots/${bot.name}/logs?lines=300`);
      const data = await res.json();
      setLogContent(data);
    } catch (err) {
      setLogContent({ error: err.message });
    }
  }, [bot]);

  const downloadLog = useCallback((source) => {
    if (!bot) return;
    window.open(`/api/bots/${bot.name}/logs/download?source=${source}`, '_blank');
  }, [bot]);

  if (!bot) {
    return (
      <div style={{ ...styles.root, display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
        <span style={{ color: "#94a3b8", fontSize: 16 }}>No bots configured. Edit dashboard/dashboard.yaml to add bots.</span>
      </div>
    );
  }

  return (
    <div style={styles.root}>
      {/* ── Tab Bar ────────────────────── */}
      <div style={styles.tabBar}>
        {bots.map((b, i) => (
          <div key={b.name} style={styles.tab(i === activeTab)} onClick={() => setActiveTab(i)}>
            <span style={styles.tabDot(b.status)} />
            {b.name}
            <span style={{ color: "#64748b", fontSize: 11, fontWeight: 400 }}>{b.symbol}</span>
          </div>
        ))}
      </div>

      <div style={styles.container}>
        {/* ── Header Row ──────────────── */}
        <div style={styles.headerRow}>
          <div style={styles.nameBlock}>
            <span style={{ fontSize: 20, fontWeight: 700 }}>{bot.name}</span>
            {bot.version && <span style={styles.versionTag}>{bot.version}</span>}
            {bot.exchange && <span style={styles.exchangeTag}>{bot.exchange}</span>}
            <span style={styles.assetTag}>{bot.symbol}</span>
            <StatusDot status={bot.status} />
            <PositionBadge position={bot.position || "FLAT"} />
            {bot.paused && <span style={{ color: "#f59e0b", fontSize: 12, fontWeight: 600 }}>PAUSED</span>}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            {!isRunning && bot.status !== "starting" && bot.status !== "stopping" && (
              <button style={styles.toggleBtn(false)} onClick={() => botAction("start")} disabled={actionLoading}>
                ▶ Start
              </button>
            )}
            {(bot.status === "starting" || bot.status === "stopping") && (
              <button style={styles.actionBtn("#94a3b8")} disabled>
                {bot.status === "starting" ? "Starting..." : "Stopping..."}
              </button>
            )}
            {isRunning && !bot.paused && (
              <button style={styles.actionBtn("#f59e0b")} onClick={() => botAction("pause")} disabled={actionLoading}>
                ⏸ Pause
              </button>
            )}
            {isRunning && bot.paused && (
              <button style={styles.actionBtn("#3b82f6")} onClick={() => botAction("resume")} disabled={actionLoading}>
                ▶ Resume
              </button>
            )}
            {isRunning && bot.position !== "FLAT" && (
              <button style={styles.actionBtn("#f59e0b")} onClick={() => botAction("exit-trade")} disabled={actionLoading}>
                ✕ Exit Trade
              </button>
            )}
            {isRunning && (
              <button style={styles.toggleBtn(true)} onClick={() => botAction("stop")} disabled={actionLoading}>
                ⏹ Quit
              </button>
            )}
            <button style={styles.actionBtn("#64748b")} onClick={fetchLogs}>
              Logs
            </button>
          </div>
        </div>

        {/* ── Status Cards ────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, padding: "8px 0" }}>
          <div style={styles.cardSmall}>
            <div style={styles.label}>Portfolio Value</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: "#f1f5f9" }}>${(bot.portfolioValue || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
          </div>
          <div style={styles.cardSmall}>
            <div style={styles.label}>PnL YTD</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}><PnlBadge value={pnlStats.ytd} /></div>
          </div>
          <div style={styles.cardSmall}>
            <div style={styles.label}>PnL MTD</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}><PnlBadge value={pnlStats.mtd} /></div>
          </div>
          <div style={styles.cardSmall}>
            <div style={styles.label}>PnL WTD</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}><PnlBadge value={pnlStats.wtd} /></div>
          </div>
          <div style={styles.cardSmall}>
            <div style={styles.label}>Last Price</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: "#f1f5f9" }}>${(bot.lastPrice || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
          </div>
          {bot.position && bot.position !== "FLAT" && (
            <div style={styles.cardSmall}>
              <div style={styles.label}>Cash</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: "#f1f5f9" }}>${(bot.cash || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
            </div>
          )}
        </div>

        {/* ── Mini Charts Row ─────────── */}
        <div style={{ display: "flex", gap: 12, padding: "4px 0 8px", flexWrap: "wrap" }}>
          <div style={styles.miniChartCard}>
            <div style={styles.label}>Portfolio Value Over Time</div>
            {pnlStats.portfolioHistory.length > 0 ? (
              <ResponsiveContainer width="100%" height={100}>
                <AreaChart data={pnlStats.portfolioHistory}>
                  <defs><linearGradient id="portGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} /><stop offset="100%" stopColor="#3b82f6" stopOpacity={0} /></linearGradient></defs>
                  <Area type="monotone" dataKey="value" stroke="#3b82f6" fill="url(#portGrad)" strokeWidth={1.5} dot={false} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`$${v.toLocaleString()}`, "Value"]} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 100, display: "flex", alignItems: "center", justifyContent: "center", color: "#475569", fontSize: 12 }}>No data yet</div>
            )}
          </div>
          <div style={styles.miniChartCard}>
            <div style={{ display: "flex", gap: 16, marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: "#22c55e" }}>■ Long PnL%</span>
              <span style={{ fontSize: 11, color: "#ef4444" }}>■ Short PnL%</span>
              <span style={{ fontSize: 11, color: "#f59e0b" }}>— Win Rate</span>
            </div>
            {pnlStats.pnlByWeek.length > 0 ? (
              <ResponsiveContainer width="100%" height={120}>
                <ComposedChart data={pnlStats.pnlByWeek} barGap={0} barCategoryGap="20%">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="week" tick={{ fill: "#94a3b8", fontSize: 9 }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="pnl" tick={{ fill: "#94a3b8", fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={(v) => `${v}%`} />
                  <YAxis yAxisId="wr" orientation="right" tick={{ fill: "#f59e0b", fontSize: 9 }} axisLine={false} tickLine={false} domain={[30, 80]} tickFormatter={(v) => `${v}%`} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v, name) => [`${v}%`, name === "longPnl" ? "Long PnL" : name === "shortPnl" ? "Short PnL" : "Win Rate"]} />
                  <Bar yAxisId="pnl" dataKey="longPnl" stackId="pnl" fill="#22c55e" radius={[0, 0, 0, 0]} />
                  <Bar yAxisId="pnl" dataKey="shortPnl" stackId="pnl" fill="#ef4444" radius={[2, 2, 0, 0]} />
                  <Line yAxisId="wr" type="monotone" dataKey="winRate" stroke="#f59e0b" strokeWidth={1.5} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 120, display: "flex", alignItems: "center", justifyContent: "center", color: "#475569", fontSize: 12 }}>No completed trades</div>
            )}
          </div>
        </div>

        {/* ── Price Chart ─────────────── */}
        <div style={styles.card}>
          <div style={styles.chartHeader}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>Price Chart — {bot.symbol}</span>
            <div style={{ display: "flex", gap: 4 }}>
              {["1W", "1M", "3M", "6M", "1Y"].map(r => (
                <button key={r} style={styles.rangeBtn(r === chartRange)} onClick={() => setChartRange(r)}>{r}</button>
              ))}
            </div>
          </div>
          <PriceChart ohlcv={ohlcv} trades={trades} range={chartRange} />
        </div>

        {/* ── Trades Table ────────────── */}
        <div style={{ ...styles.card, marginTop: 16, marginBottom: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>Recent Trades ({trades.length})</span>
            <button style={styles.downloadBtn} onClick={downloadCSV}>
              ⬇ Download CSV
            </button>
          </div>
          <div style={{ maxHeight: 480, overflowY: "auto", borderRadius: 6 }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Date</th>
                  <th style={styles.th}>Action</th>
                  <th style={styles.th}>Symbol</th>
                  <th style={styles.th}>Qty</th>
                  <th style={styles.th}>Price</th>
                  <th style={styles.th}>Cash</th>
                  <th style={styles.th}>Portfolio</th>
                </tr>
              </thead>
              <tbody>
                {trades.length === 0 ? (
                  <tr><td colSpan={7} style={{ ...styles.td, textAlign: "center", color: "#475569" }}>No trades yet</td></tr>
                ) : trades.map((t, i) => (
                  <tr key={i} style={{ background: i % 2 === 0 ? "transparent" : "#0f172a40" }}>
                    <td style={styles.td}>{t.date}</td>
                    <td style={styles.td}>
                      <PositionBadge position={t.action === "BUY" || t.action === "SELL" ? "LONG" : t.action === "SHORT" || t.action === "COVER" ? "SHORT" : "FLAT"} />
                      <span style={{ marginLeft: 6, fontSize: 11, color: "#94a3b8" }}>{t.action}</span>
                    </td>
                    <td style={styles.td}>{t.symbol}</td>
                    <td style={styles.td}>{t.qty.toFixed(6)}</td>
                    <td style={styles.td}>${t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                    <td style={styles.td}>${t.cashBalance.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                    <td style={styles.td}>${t.portfolioValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ── Log Viewer Modal ────────── */}
      {showLogs && (
        <div style={styles.logOverlay} onClick={() => setShowLogs(false)}>
          <div style={styles.logModal} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontWeight: 700, fontSize: 16 }}>Bot Logs — {bot?.name}</span>
                <div style={{ display: "flex", gap: 4 }}>
                  {["process", "python"].map(s => (
                    <button key={s} style={styles.rangeBtn(logSource === s)} onClick={() => setLogSource(s)}>
                      {s === "process" ? "Process (stdout)" : "Python Log"}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button style={styles.downloadBtn} onClick={() => downloadLog(logSource)}>Download</button>
                <button style={{ ...styles.downloadBtn, color: "#ef4444" }} onClick={() => setShowLogs(false)}>Close</button>
              </div>
            </div>
            <pre style={styles.logContent}>
              {logContent === null
                ? "Loading..."
                : logContent.error
                  ? `Error: ${logContent.error}`
                  : (logContent[logSource] || "No log file found.")}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────

const tooltipStyle = { background: "#1e293b", border: "1px solid #334155", borderRadius: 6, fontSize: 12, color: "#e2e8f0" };

const styles = {
  root: { fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", background: "#0a0e17", color: "#e2e8f0", minHeight: "100vh", padding: 0 },
  container: { maxWidth: 1400, margin: "0 auto", padding: "0 16px" },
  tabBar: { display: "flex", gap: 0, background: "#111827", borderBottom: "1px solid #1e293b", padding: "0 16px", overflowX: "auto" },
  tab: (active) => ({ padding: "12px 20px", cursor: "pointer", background: active ? "#1e293b" : "transparent", borderBottom: active ? "2px solid #3b82f6" : "2px solid transparent", color: active ? "#f1f5f9" : "#64748b", fontWeight: active ? 600 : 400, fontSize: 13, whiteSpace: "nowrap", transition: "all .15s", display: "flex", alignItems: "center", gap: 8 }),
  tabDot: (status) => ({ width: 6, height: 6, borderRadius: "50%", background: status === "running" ? "#22c55e" : status === "crashed" ? "#ef4444" : "#94a3b8" }),
  headerRow: { display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, padding: "16px 0 8px" },
  nameBlock: { display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" },
  versionTag: { background: "#1e293b", color: "#94a3b8", borderRadius: 4, padding: "2px 8px", fontSize: 11, fontWeight: 500 },
  exchangeTag: { background: "#3b82f620", color: "#60a5fa", borderRadius: 4, padding: "2px 8px", fontSize: 11, fontWeight: 600 },
  assetTag: { fontSize: 16, fontWeight: 700, color: "#f1f5f9" },
  toggleBtn: (running) => ({ background: running ? "#dc262620" : "#22c55e20", color: running ? "#ef4444" : "#22c55e", border: `1px solid ${running ? "#ef444460" : "#22c55e60"}`, borderRadius: 6, padding: "8px 20px", cursor: "pointer", fontWeight: 600, fontSize: 13, transition: "all .15s" }),
  actionBtn: (color) => ({ background: `${color}20`, color, border: `1px solid ${color}60`, borderRadius: 6, padding: "8px 16px", cursor: "pointer", fontWeight: 600, fontSize: 13, transition: "all .15s" }),
  card: { background: "#111827", border: "1px solid #1e293b", borderRadius: 8, padding: 16 },
  cardSmall: { background: "#111827", border: "1px solid #1e293b", borderRadius: 8, padding: "12px 16px" },
  label: { fontSize: 11, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 },
  chartHeader: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12, flexWrap: "wrap", gap: 8 },
  rangeBtn: (active) => ({ padding: "4px 14px", borderRadius: 4, fontSize: 12, fontWeight: 600, cursor: "pointer", background: active ? "#3b82f6" : "#1e293b", color: active ? "#fff" : "#94a3b8", border: "none", transition: "all .15s" }),
  miniChartCard: { background: "#111827", border: "1px solid #1e293b", borderRadius: 8, padding: "12px 16px", flex: 1, minWidth: 200 },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 13 },
  th: { textAlign: "left", padding: "10px 12px", borderBottom: "1px solid #1e293b", color: "#94a3b8", fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 600, position: "sticky", top: 0, background: "#111827", zIndex: 1 },
  td: { padding: "9px 12px", borderBottom: "1px solid #1e293b15", fontVariantNumeric: "tabular-nums" },
  downloadBtn: { background: "#1e293b", color: "#94a3b8", border: "1px solid #334155", borderRadius: 6, padding: "6px 16px", cursor: "pointer", fontSize: 12, fontWeight: 500, display: "flex", alignItems: "center", gap: 6 },
  logOverlay: { position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 1000, display: "flex", justifyContent: "center", alignItems: "center" },
  logModal: { background: "#0f172a", border: "1px solid #334155", borderRadius: 12, width: "90vw", maxWidth: 1100, maxHeight: "85vh", padding: 20, display: "flex", flexDirection: "column" },
  logContent: { flex: 1, overflow: "auto", background: "#020617", border: "1px solid #1e293b", borderRadius: 6, padding: 16, fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: 12, lineHeight: 1.6, color: "#cbd5e1", whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0, maxHeight: "65vh" },
};
