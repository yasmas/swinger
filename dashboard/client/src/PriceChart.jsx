import { useEffect, useRef } from "react";
import { createChart, CandlestickSeries, LineSeries, HistogramSeries, AreaSeries, ColorType, createSeriesMarkers } from "lightweight-charts";

const RVOL_PERIOD = 20;

function computeRealisedVol(ohlcv, tzOffsetSec) {
  if (ohlcv.length < RVOL_PERIOD + 1) return [];
  const logReturns = [];
  for (let i = 1; i < ohlcv.length; i++) {
    logReturns.push(Math.log(ohlcv[i].close / ohlcv[i - 1].close));
  }
  // Estimate bar interval to annualize
  const intervalMs = ohlcv[1].timestamp - ohlcv[0].timestamp;
  const barsPerYear = (365.25 * 24 * 3600 * 1000) / intervalMs;
  const annFactor = Math.sqrt(barsPerYear);

  const result = [];
  for (let i = RVOL_PERIOD - 1; i < logReturns.length; i++) {
    const window = logReturns.slice(i - RVOL_PERIOD + 1, i + 1);
    const mean = window.reduce((s, v) => s + v, 0) / RVOL_PERIOD;
    const variance = window.reduce((s, v) => s + (v - mean) ** 2, 0) / (RVOL_PERIOD - 1);
    result.push({
      time: Math.floor(ohlcv[i + 1].timestamp / 1000) + tzOffsetSec,
      value: Math.sqrt(variance) * annFactor * 100,
    });
  }
  return result;
}

const MARKER_CONFIG = {
  BUY:   { color: "#22c55e", shape: "arrowUp",   position: "belowBar", text: "BUY" },
  SELL:  { color: "#ef4444", shape: "arrowDown",  position: "aboveBar", text: "SELL" },
  SHORT: { color: "#ef4444", shape: "arrowDown",  position: "aboveBar", text: "SHORT" },
  COVER: { color: "#22c55e", shape: "arrowUp",    position: "belowBar", text: "COVER" },
};

function buildSkipMarkerFromTrade(trade) {
  const details = trade?.details && typeof trade.details === "object" ? trade.details : null;
  if (!details) return null;

  const reason = String(details.reason || "");
  const indicators = details.indicators && typeof details.indicators === "object" ? details.indicators : {};
  const flipInfo = indicators.flip_vol_ratio && typeof indicators.flip_vol_ratio === "object"
    ? indicators.flip_vol_ratio
    : {};

  if (reason !== "st_flip_ratio_rejected_hold") return null;

  const ratio = flipInfo.ratio;
  const ratioMin = flipInfo.ratio_min;
  const heldStop = flipInfo.held_stop_pct;
  const tooltip = [
    "HOLD on skipped ST flip",
    ratio != null ? `Ratio: ${Number(ratio).toFixed(4)}` : "Ratio: n/a",
    ratioMin != null ? `Threshold: ${Number(ratioMin).toFixed(4)}` : "Threshold: n/a",
  ];
  if (heldStop != null) {
    tooltip.push(`Safety stop: ${Number(heldStop).toFixed(4)}%`);
  }

  return {
    color: "#f59e0b",
    shape: "circle",
    position: "inBar",
    text: "HOLD",
    tooltip: tooltip.join("\n"),
  };
}

function buildHoldMarkerFromDiagnostic(diag) {
  if (!diag || String(diag.action || "").toUpperCase() !== "HOLD") return null;
  if (String(diag.reason || "") !== "st_flip_ratio_rejected_hold") return null;

  const tooltip = [
    "HOLD on skipped ST flip",
    Number.isFinite(diag.close) ? `Close: ${Number(diag.close).toFixed(2)}` : "Close: n/a",
    Number.isFinite(diag.stLine) ? `ST line: ${Number(diag.stLine).toFixed(2)}` : "ST line: n/a",
    typeof diag.stBullish === "boolean" ? `ST trend: ${diag.stBullish ? "Bullish" : "Bearish"}` : "ST trend: n/a",
    Number.isFinite(diag.flipVolRatio) ? `Ratio: ${Number(diag.flipVolRatio).toFixed(4)}` : "Ratio: n/a",
    Number.isFinite(diag.flipVolRatioThreshold)
      ? `Threshold: ${Number(diag.flipVolRatioThreshold).toFixed(4)}`
      : "Threshold: n/a",
  ];
  if (Number.isFinite(diag.heldFlipStopPct)) {
    tooltip.push(`Safety stop: ${Number(diag.heldFlipStopPct).toFixed(4)}%`);
  }
  if (diag.flipVolRatioRegimeMode) {
    tooltip.push(`Regime mode: ${diag.flipVolRatioRegimeMode}`);
  }
  if (Number.isFinite(diag.flipVolRatioRegimeWeight)) {
    tooltip.push(`Regime weight: ${Number(diag.flipVolRatioRegimeWeight).toFixed(4)}`);
  }

  return {
    color: "#f59e0b",
    shape: "circle",
    position: "inBar",
    text: "HOLD",
    tooltip: tooltip.join("\n"),
  };
}

// How many seconds of data to show by default per range
const VISIBLE_SECONDS = {
  "1W": 24 * 3600,       // 1 day
  "1M": 5 * 24 * 3600,   // 5 days
  "3M": 14 * 24 * 3600,  // 2 weeks
  "6M": 30 * 24 * 3600,  // 1 month
  "1Y": 60 * 24 * 3600,  // 2 months
};

export default function PriceChart({ ohlcv, trades, diagnostics = [], range = "1M", supertrend = [] }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const stSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const rvolSeriesRef = useRef(null);
  const markersRef = useRef(null);
  const tooltipRef = useRef(null);
  // Store trade markers data for tooltip lookup
  const tradeMapRef = useRef(new Map());

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#111827" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: "#334155", style: 3 },
        horzLine: { color: "#334155", style: 3 },
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
      },
      rightPriceScale: {
        borderColor: "#1e293b",
      },
      leftPriceScale: {
        visible: true,
        borderColor: "#1e293b",
      },
      handleScroll: true,
      handleScale: true,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    const stSeries = chart.addSeries(LineSeries, {
      lineWidth: 2,
      crosshairMarkerVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });

    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
      drawTicks: false,
      borderVisible: false,
    });

    const rvolSeries = chart.addSeries(AreaSeries, {
      topColor: "rgba(99, 102, 241, 0.35)",
      bottomColor: "rgba(99, 102, 241, 0.0)",
      lineColor: "rgba(99, 102, 241, 0.6)",
      lineWidth: 1,
      priceScaleId: "left",
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    seriesRef.current = series;
    stSeriesRef.current = stSeries;
    volumeSeriesRef.current = volumeSeries;
    rvolSeriesRef.current = rvolSeries;

    // Resize observer
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      chart.applyOptions({ width, height });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      stSeriesRef.current = null;
      volumeSeriesRef.current = null;
      rvolSeriesRef.current = null;
      if (markersRef.current) {
        markersRef.current.detach();
        markersRef.current = null;
      }
    };
  }, []);

  // Update data when ohlcv, trades, diagnostics, or supertrend change
  useEffect(() => {
    const series = seriesRef.current;
    const stSeries = stSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    if (ohlcv.length === 0) {
      series.setData([]);
      if (stSeries) stSeries.setData([]);
      if (volumeSeries) volumeSeries.setData([]);
      if (rvolSeriesRef.current) rvolSeriesRef.current.setData([]);
      if (markersRef.current) {
        markersRef.current.detach();
        markersRef.current = null;
      }
      return;
    }

    // Convert to lightweight-charts format (time in seconds, shifted to local time)
    // lightweight-charts treats all timestamps as UTC, so we offset by the local TZ
    const tzOffsetSec = new Date().getTimezoneOffset() * -60;
    const candleData = ohlcv.map((d) => ({
      time: Math.floor(d.timestamp / 1000) + tzOffsetSec,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    series.setData(candleData);

    // Volume bars
    if (volumeSeries) {
      const volumeData = ohlcv.map((d) => ({
        time: Math.floor(d.timestamp / 1000) + tzOffsetSec,
        value: d.volume,
        color: d.close >= d.open ? "#22c55e30" : "#ef444430",
      }));
      volumeSeries.setData(volumeData);
    }

    // Realised volatility overlay
    if (rvolSeriesRef.current) {
      rvolSeriesRef.current.setData(computeRealisedVol(ohlcv, tzOffsetSec));
    }

    // Supertrend overlay
    if (stSeries && supertrend.length > 0) {
      const stData = supertrend.map((d) => ({
        time: Math.floor(d.time / 1000) + tzOffsetSec,
        value: d.value,
        color: d.color,
      }));
      stSeries.setData(stData);
    } else if (stSeries) {
      stSeries.setData([]);
    }

    // Clean up old markers
    if (markersRef.current) {
      markersRef.current.detach();
      markersRef.current = null;
    }

    // Build markers from trades and diagnostics
    tradeMapRef.current = new Map();
    if (candleData.length > 0) {
      const markers = [];
      const addMarkerEntry = (ts, cfg, entry) => {
        if (isNaN(ts)) return;

        let bestTime = candleData[0].time;
        let bestDiff = Infinity;
        for (const c of candleData) {
          const diff = Math.abs(c.time - ts);
          if (diff < bestDiff) {
            bestDiff = diff;
            bestTime = c.time;
          }
        }

        markers.push({
          time: bestTime,
          position: cfg.position,
          color: cfg.color,
          shape: cfg.shape,
          text: cfg.text,
        });

        if (!tradeMapRef.current.has(bestTime)) {
          tradeMapRef.current.set(bestTime, []);
        }
        tradeMapRef.current.get(bestTime).push(entry);
      };

      for (const t of trades) {
        if (!t.date || !t.action) continue;
        const tradeTs = Math.floor(new Date(t.date).getTime() / 1000) + tzOffsetSec;
        const cfg = MARKER_CONFIG[t.action] || buildSkipMarkerFromTrade(t);
        if (!cfg) continue;
        addMarkerEntry(tradeTs, cfg, {
          action: t.action,
          price: t.price,
          date: t.date,
          qty: t.qty,
          text: cfg.text,
          tooltip: cfg.tooltip || null,
        });
      }

      for (const d of diagnostics) {
        const cfg = buildHoldMarkerFromDiagnostic(d);
        if (!cfg) continue;
        const diagTs = Math.floor(new Date(d.date || d.dateUtc).getTime() / 1000) + tzOffsetSec;
        addMarkerEntry(diagTs, cfg, {
          action: "HOLD",
          price: d.close,
          date: d.date || d.dateUtc,
          qty: 0,
          text: cfg.text,
          tooltip: cfg.tooltip,
        });
      }

      if (markers.length > 0) {
        markers.sort((a, b) => a.time - b.time);
        markersRef.current = createSeriesMarkers(series, markers);
      }
    }

    // Set visible range: show the latest N seconds of data, scrolled to the right
    const visibleSec = VISIBLE_SECONDS[range] || VISIBLE_SECONDS["1M"];
    const lastTime = candleData[candleData.length - 1].time;
    const fromTime = lastTime - visibleSec;
    chart.timeScale().setVisibleRange({ from: fromTime, to: lastTime });
  }, [ohlcv, trades, diagnostics, range, supertrend]);

  // Tooltip on crosshair move
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;

    const handler = (param) => {
      const tooltip = tooltipRef.current;
      if (!tooltip) return;

      if (!param.time || !param.point) {
        tooltip.style.display = "none";
        return;
      }

      const tradeEntries = tradeMapRef.current.get(param.time);
      if (!tradeEntries || tradeEntries.length === 0) {
        tooltip.style.display = "none";
        return;
      }

      const lines = tradeEntries.map((t) => {
        if (t.tooltip) return t.tooltip;
        const d = new Date(t.date);
        const timeStr = d.toLocaleString(undefined, {
          month: "short", day: "numeric",
          hour: "2-digit", minute: "2-digit",
          hour12: false,
        });
        return `${t.action}  $${t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}  ${timeStr}`;
      });

      tooltip.textContent = lines.join("\n");
      tooltip.style.display = "block";

      // Position tooltip near the crosshair
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      let left = param.point.x + 16;
      let top = param.point.y - 12;
      // Keep tooltip within chart bounds
      if (left + 220 > rect.width) left = param.point.x - 230;
      if (top < 0) top = 4;
      tooltip.style.left = left + "px";
      tooltip.style.top = top + "px";
    };

    chart.subscribeCrosshairMove(handler);
    return () => chart.unsubscribeCrosshairMove(handler);
  }, []);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: 400, borderRadius: 6, overflow: "hidden", position: "relative" }}
    >
      <div
        ref={tooltipRef}
        style={{
          display: "none",
          position: "absolute",
          zIndex: 10,
          background: "#1e293bee",
          border: "1px solid #334155",
          borderRadius: 4,
          padding: "6px 10px",
          fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
          color: "#e2e8f0",
          whiteSpace: "pre",
          pointerEvents: "none",
        }}
      />
    </div>
  );
}
