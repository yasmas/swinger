import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, LineSeries, AreaSeries, HistogramSeries, ColorType } from "lightweight-charts";

const LineStyleDotted = 1;

function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

const RANGE_MAP = { "1W": "5m", "1M": "1h", "6M": "4h" };

function makeChartOpts() {
  return {
    autoSize: true,
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
      scaleMargins: { top: 0.08, bottom: 0.08 },
    },
    leftPriceScale: { visible: false },
    handleScroll: true,
    handleScale: true,
  };
}

function shiftLine(arr) {
  if (!arr || !arr.length) return [];
  const tzOffsetSec = new Date().getTimezoneOffset() * -60;
  return arr.map((d) => ({ ...d, time: d.time + tzOffsetSec }));
}

if (typeof window !== "undefined" && !window.__lwcErrorSuppressed) {
  window.__lwcErrorSuppressed = true;
  window.addEventListener("error", (e) => {
    if (e.message === "Object is disposed") e.preventDefault();
  });
}

export default function SwingPartyChart({ chartData, range = "1M" }) {
  const relContainerRef = useRef(null);
  const portContainerRef = useRef(null);
  const relChartRef = useRef(null);
  const portChartRef = useRef(null);
  const relSeriesRef = useRef([]);
  const portSeriesRef = useRef(null);
  const overlayRef = useRef({ stSeries: {}, volSeries: {} });
  const [overlaySymbol, setOverlaySymbol] = useState(null);

  const tfKey = RANGE_MAP[range] || "1h";
  const tfData = chartData?.[tfKey] || {};
  const symbolsMeta = chartData?.symbols || [];
  const portfolioData = chartData?.portfolio || [];
  const symbolsKey = symbolsMeta.map((s) => s.symbol).join(",");

  useEffect(() => {
    setOverlaySymbol(null);
  }, [symbolsKey]);

  // ── Create charts once ──
  useEffect(() => {
    if (!relContainerRef.current) return;
    const chart = createChart(relContainerRef.current, makeChartOpts());
    relChartRef.current = chart;
    return () => { relChartRef.current = null; chart.remove(); };
  }, []);

  useEffect(() => {
    if (!portContainerRef.current) return;
    const chart = createChart(portContainerRef.current, makeChartOpts());
    portChartRef.current = chart;
    return () => { portChartRef.current = null; chart.remove(); };
  }, []);

  // ── Update relative chart data ──
  useEffect(() => {
    const chart = relChartRef.current;
    if (!chart || symbolsMeta.length === 0) return;

    for (const s of relSeriesRef.current) {
      try { chart.removeSeries(s); } catch {}
    }
    relSeriesRef.current = [];
    overlayRef.current = { stSeries: {}, volSeries: {} };

    for (const { symbol, color } of symbolsMeta) {
      const seg = tfData[symbol];
      if (!seg) continue;
      const muted = hexToRgba(color, 0.42);

      for (const [i, points] of (seg.solid || []).entries()) {
        const data = shiftLine(points);
        if (data.length === 0) continue;
        const s = chart.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: i === (seg.solid || []).length - 1,
          crosshairMarkerVisible: false,
        });
        s.setData(data);
        relSeriesRef.current.push(s);
      }

      for (const points of (seg.dotted || [])) {
        const data = shiftLine(points);
        if (data.length === 0) continue;
        const s = chart.addSeries(LineSeries, {
          color: muted,
          lineWidth: 1,
          lineStyle: LineStyleDotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        s.setData(data);
        relSeriesRef.current.push(s);
      }

      // ST overlay — hidden until legend toggle
      const stData = seg.st || [];
      if (stData.length > 0) {
        const clean = shiftLine(stData).map(d => ({ time: d.time, value: d.value, color: d.color }));
        const s = chart.addSeries(LineSeries, {
          color: "#ffffff",
          lineWidth: 2,
          lineStyle: LineStyleDotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          visible: false,
        });
        s.setData(clean);
        relSeriesRef.current.push(s);
        overlayRef.current.stSeries[symbol] = { series: s };
      }

      // Volume overlay — hidden until legend toggle, separate scale
      const volData = seg.volume || [];
      if (volData.length > 0) {
        const clean = shiftLine(volData).map(d => ({
          time: d.time,
          value: d.value,
          color: hexToRgba(color, 0.35),
        }));
        const s = chart.addSeries(HistogramSeries, {
          priceFormat: { type: "volume" },
          priceLineVisible: false,
          lastValueVisible: false,
          visible: false,
          priceScaleId: "vol",
        });
        s.setData(clean);
        relSeriesRef.current.push(s);
        overlayRef.current.volSeries[symbol] = s;
      }
    }

    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
      drawTicks: false,
      borderVisible: false,
    });

    chart.timeScale().fitContent();
  }, [tfData, symbolsMeta, range]);

  // ── Toggle ST + volume overlays (legend click) — NOT tfData (would reset on data load)
  useEffect(() => {
    const { stSeries, volSeries } = overlayRef.current;

    for (const [sym, entry] of Object.entries(stSeries)) {
      try { entry.series.applyOptions({ visible: sym === overlaySymbol }); } catch {}
    }
    for (const [sym, series] of Object.entries(volSeries)) {
      try { series.applyOptions({ visible: sym === overlaySymbol }); } catch {}
    }
  }, [overlaySymbol]);

  // ── Update portfolio chart data ──
  useEffect(() => {
    const chart = portChartRef.current;
    if (!chart || portfolioData.length === 0) return;

    if (portSeriesRef.current) {
      try { chart.removeSeries(portSeriesRef.current); } catch {}
    }

    const series = chart.addSeries(AreaSeries, {
      topColor: "rgba(171, 71, 188, 0.4)",
      bottomColor: "rgba(171, 71, 188, 0.05)",
      lineColor: "#ab47bc",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    series.setData(shiftLine(portfolioData));
    portSeriesRef.current = series;
    chart.timeScale().fitContent();
  }, [portfolioData]);

  const onLegendToggle = useCallback((sym) => {
    setOverlaySymbol((cur) => (cur === sym ? null : sym));
  }, []);

  return (
    <div>
      {/* Legend — click to toggle ST + volume for one ticker */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 10px", padding: "0 0 10px", fontSize: 12, color: "#cbd5e1" }}>
        {symbolsMeta.map(({ symbol, color }) => {
          const active = overlaySymbol === symbol;
          return (
            <button
              key={symbol}
              type="button"
              onClick={() => onLegendToggle(symbol)}
              title={active ? "Hide Supertrend & volume" : "Show Supertrend & volume"}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                cursor: "pointer",
                padding: "5px 10px",
                borderRadius: 6,
                border: active ? `1px solid ${color}88` : "1px solid #334155",
                background: active ? "rgba(255,255,255,0.1)" : "#1e293b",
                color: "#e2e8f0",
                font: "inherit",
                transition: "background 0.15s, border-color 0.15s",
              }}
            >
              <span style={{ width: 14, height: 3, borderRadius: 1, background: color, flexShrink: 0 }} />
              <span style={{ fontWeight: 600, letterSpacing: "0.02em" }}>{symbol}</span>
              <span style={{ fontSize: 10, color: active ? "#94a3b8" : "#64748b", marginLeft: 2 }}>
                ST+Vol
              </span>
            </button>
          );
        })}
      </div>

      {/* Top chart: normalized % */}
      <div
        ref={relContainerRef}
        style={{ width: "100%", height: 400, borderRadius: 6, overflow: "hidden" }}
      />

      {/* Bottom chart: portfolio value */}
      <div style={{ fontSize: 11, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 1, margin: "12px 0 4px", fontWeight: 500 }}>
        Portfolio Value
      </div>
      <div
        ref={portContainerRef}
        style={{ width: "100%", height: 160, borderRadius: 6, overflow: "hidden" }}
      />
    </div>
  );
}
