import { useEffect, useRef } from "react";
import { createChart, CandlestickSeries, ColorType, createSeriesMarkers } from "lightweight-charts";

const MARKER_CONFIG = {
  BUY:   { color: "#22c55e", shape: "arrowUp",   position: "belowBar", text: "BUY" },
  SELL:  { color: "#ef4444", shape: "arrowDown",  position: "aboveBar", text: "SELL" },
  SHORT: { color: "#ef4444", shape: "arrowDown",  position: "aboveBar", text: "SHORT" },
  COVER: { color: "#22c55e", shape: "arrowUp",    position: "belowBar", text: "COVER" },
};

// How many seconds of data to show by default per range
const VISIBLE_SECONDS = {
  "1W": 24 * 3600,       // 1 day
  "1M": 5 * 24 * 3600,   // 5 days
  "3M": 14 * 24 * 3600,  // 2 weeks
  "6M": 30 * 24 * 3600,  // 1 month
  "1Y": 60 * 24 * 3600,  // 2 months
};

export default function PriceChart({ ohlcv, trades, range = "1M" }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersRef = useRef(null);

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

    chartRef.current = chart;
    seriesRef.current = series;

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
      if (markersRef.current) {
        markersRef.current.detach();
        markersRef.current = null;
      }
    };
  }, []);

  // Update data when ohlcv or trades change
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || ohlcv.length === 0) return;

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

    // Clean up old markers
    if (markersRef.current) {
      markersRef.current.detach();
      markersRef.current = null;
    }

    // Build markers from trades
    if (trades.length > 0 && candleData.length > 0) {
      const markers = [];
      for (const t of trades) {
        if (!t.date || !t.action) continue;
        const tradeTs = Math.floor(new Date(t.date).getTime() / 1000) + tzOffsetSec;
        if (isNaN(tradeTs)) continue;

        const cfg = MARKER_CONFIG[t.action];
        if (!cfg) continue;

        // Find the nearest candle time
        let bestTime = candleData[0].time;
        let bestDiff = Infinity;
        for (const c of candleData) {
          const diff = Math.abs(c.time - tradeTs);
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
      }

      // Markers must be sorted by time
      markers.sort((a, b) => a.time - b.time);
      markersRef.current = createSeriesMarkers(series, markers);
    }

    // Set visible range: show the latest N seconds of data, scrolled to the right
    const visibleSec = VISIBLE_SECONDS[range] || VISIBLE_SECONDS["1M"];
    const lastTime = candleData[candleData.length - 1].time;
    const fromTime = lastTime - visibleSec;
    chart.timeScale().setVisibleRange({ from: fromTime, to: lastTime });
  }, [ohlcv, trades, range]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: 400, borderRadius: 6, overflow: "hidden" }}
    />
  );
}
