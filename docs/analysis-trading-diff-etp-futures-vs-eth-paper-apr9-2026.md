# Live ETP Futures vs Paper ETH — Trade Analysis (Apr 9–17, 2026)

## Account Setup

| | Live (ETP Futures) | Paper (ETH Perp) |
|---|---|---|
| Symbol | ETP-20DEC30-CDE | ETH-PERP-INTX |
| Contract size | 0.1 ETH/contract | 1 ETH/unit |
| Position size | 3 contracts (Apr 9–12), 6 contracts (Apr 12+) | Full account (~47–50 ETH) |
| Notional per trade | ~$660–$1,320 | ~$100k–$118k |
| Starting equity | ~$1,486 | $100,000 |
| Strategy config | eth_live.yaml | eth_live.yaml (same) |
| Started | Apr 9 | Apr 7 |
| Fees | Real Coinbase fees (~$1–2/leg) | 0.05% simulated |

---

## Performance (Overlapping Period: Apr 9–17)

| Account | Start Equity | End Equity | Return |
|---|---|---|---|
| Paper | $105,881 (Apr 9) | $117,160 (Apr 17 17:31) | **+10.6%** |
| Live | ~$1,486 (Apr 9) | ~$1,480 (Apr 17 emergency close) | **−0.4%** |

The core strategy signals are the same on both accounts — the 14+ matched trades have nearly identical price-return% per ETH. The performance gap is driven by three factors:

---

## Reasons for Performance Gap

### 1. Fees are proportionally enormous on the live account (~−3 to −5%)
Real Coinbase fees of ~$1–2 per leg across ~42 trade legs = **~$50–80 total fees on a $1,486 account** (3–5% drag). Paper's simulated fees are negligible by comparison. This is the single largest driver of the gap and is a function of account size, not strategy quality.

### 2. ETP futures vs ETH-PERP price basis caused signal divergences (~−2% to +4% net)
ETP-20DEC30-CDE and ETH-PERP-INTX track the same underlying but differ in price. Both accounts compute the same Supertrend indicator, but on different price series. When the two instruments diverge at a bar boundary, one account flips and the other doesn't. This caused two notable events:

**Apr 16 — ETP smoother, live benefited (+~3.6%):**
At 13:30 UTC, ETH-PERP's Supertrend flipped bullish; ETP's did not. 

**Apr 17 — ETP noisier, live hurt (−~2.2%):**
Around 11:00 UTC, ETP's Supertrend fired a bearish flip that ETH-PERP did not. 

Net basis effect across the period: roughly **+1.4% for live** (the Apr 16 savings outweighed the Apr 17 cost), but this is coincidental and unreliable.

### 3. First trade entry was 21 minutes late with a market fill (−0.62%)
When the live bot started on Apr 9, it missed the clean SHORT entry at 2221 (paper's fill). By the time live executed, price had fallen to 2207.50 and the order triggered as a market fill due to adverse price movement (−31.6 bps slippage). Paper won +0.12% on this trade; live lost −0.50%.

### 4. One slippage event on a LONG exit (−0.50%)
Trade 6 (Apr 11 LONG): live exited at 2293 via market timeout (−48.8 bps slippage). Paper got a clean limit fill at 2304.31. Same signal, different execution quality.

---

## Trade-by-Trade Table (Overlapping Period Only)

PnL% = price return per ETH (direction-adjusted). Timestamps in PDT (UTC−7).

| # | Dir | Live Entry → Exit | Live PnL% | Paper Entry → Exit | Paper PnL% | Δ Live−Paper | Notes |
|---|-----|-------------------|-----------|-------------------|------------|--------------|-------|
| A | LONG | **MISSING** | — | Apr 9 08:36 @2215.70 → @2219.69 | +0.18% | −0.18% | Live not yet started |
| 1/B | SHORT | Apr 9 10:32 @2207.50 → Apr 10 04:32 @2218.50 | **−0.50%** | Apr 9 10:11 @2221.18 → Apr 10 04:31 @2218.53 | +0.12% | **−0.62%** | Live 21 min late; market fill (adverse move); paper clean limit |
| 2/C | LONG | Apr 10 04:36 @2216.50 → @2225.00 | +0.38% | Apr 10 04:38 @2219.55 → @2225.80 | +0.28% | +0.10% | Paper's abort fill slightly worse entry |
| 3/D | SHORT | Apr 10 09:06 @2226.50 → @2254.50 | −1.26% | Apr 10 09:06 @2226.83 → @2254.31 | −1.24% | −0.02% | Near identical |
| 4/E | LONG | Apr 10 13:06 @2256.00 → @2230.00 | −1.15% | Apr 10 13:06 @2255.48 → @2231.91 | −1.05% | −0.11% | Near identical |
| 5/F | SHORT | Apr 10 23:06 @2233.00 → @2249.50 | −0.74% | Apr 10 23:06 @2234.12 → @2250.31 | −0.73% | −0.01% | Near identical |
| 6/G | LONG | Apr 11 08:38 @2249.50 → @2293.00 | +1.93% | Apr 11 08:36 @2249.62 → @2304.31 | +2.43% | **−0.50%** | Live market timeout at exit; −48.8 bps slippage vs paper's clean fill |
| 7/H | SHORT | Apr 11 13:36 @2290.00 → @2206.50 | +3.65% | Apr 11 13:36 @2290.48 → @2204.68 | +3.75% | −0.10% | Near identical |
| 8/I | LONG | Apr 12 11:07 @2207.50 → @2188.50 | −0.86% | Apr 12 11:06 @2205.13 → @2188.67 | −0.75% | −0.11% | Near identical |
| 9/J | SHORT | Apr 12 15:36 @2192.50 → @2196.50 | −0.18% | Apr 12 15:36 @2191.31 → @2196.90 | −0.26% | +0.08% | Near identical |
| 10/K | LONG | Apr 13 06:36 @2193.00 → @2356.50 | +7.46% | Apr 13 06:36 @2192.65 → @2356.43 | +7.47% | −0.01% | Near identical — big winner |
| 11/L | SHORT | Apr 14 08:08 @2350.50 → @2338.00 | +0.53% | Apr 14 08:06 @2351.68 → @2337.80 | +0.59% | −0.06% | Near identical |
| 12/M | LONG | Apr 15 05:06 @2337.50 → @2353.00 | +0.66% | Apr 15 05:06 @2336.28 → @2353.68 | +0.75% | −0.08% | Near identical |
| 13 | SHORT | Apr 15 14:36 @2350.50 → **Apr 16 09:01 @2340.00** | **+0.45%** | — | — | — | Live held through Apr 16 whipsaw — see N/O/P |
| N | SHORT | — | — | Apr 15 14:36 @2351.21 → **Apr 16 06:31 @2349.81** | +0.06% | — | ETH-PERP flipped at 13:30 UTC; ETP did not |
| O | LONG | **SKIPPED** | — | Apr 16 06:36 @2343.48 → @2306.91 | −1.56% | +1.56% | ETP basis: paper whipsaw long, live stayed short |
| P | SHORT | **SKIPPED** | — | Apr 16 07:06 @2300.59 → @2339.36 | −1.69% | +1.69% | ETP basis: paper whipsaw short, live stayed short |
| 14/Q-a | LONG | Apr 16 09:06 @2341.00 → **Apr 16 10:01 @2316.00** | **−1.07%** | Apr 16 09:06 @2340.61 → Apr 16 19:31 @2327.94 | −0.54% | −0.53% | ETP fired another flip at 17:00 UTC; ETH-PERP did not |
| 15 | SHORT | Apr 16 10:06 @2318.50 → @2345.50 | **−1.17%** | **NO MATCH** | — | −1.17% | Live extra trade caused by ETP early exit above |
| 16/Q-b | LONG | Apr 16 12:06 @2341.50 → @2328.50 | −0.56% | (continuation of Q) | — | — | |
| 17/R | SHORT | Apr 16 19:36 @2330.00 → @2341.50 | −0.49% | Apr 16 19:36 @2329.03 → @2340.87 | −0.51% | +0.01% | Near identical; re-synced |
| 18 | LONG | Apr 17 01:36 @2352.00 → **Apr 17 04:01 @2345.00** | **−0.30%** | **NO MATCH** | — | — | ETP false flip at 11:00 UTC; paper held long |
| 19 | SHORT | Apr 17 04:06 @2347.50 → @2372.50 | **−1.07%** | **NO MATCH** | — | — | ETP-specific losing whipsaw |
| 20/S | LONG | Apr 17 06:06 @2372.50 → @2431.00 | +2.47% | Apr 17 01:36 @2351.93 → @2430.03 | +3.32% | **−0.85%** | Paper held full move; live missed first 5 hrs due to ETP false flip |
| 21/T | SHORT | Apr 17 10:36 @2435.00 → @2434.50 *(emergency close)* | +0.02% | Apr 17 10:36 @2435.95 → open | open | — | Live bot stopped |

---

## Bottom Line

The strategy is working identically on both accounts — matched trades track within ~0.1% of each other in price-return terms. The live account's near-flat return vs paper's +10.6% is explained by:

1. **Fees (−3 to −5%)** — dominant factor; real Coinbase fees are too large relative to the current position size of 3–6 contracts. To dilute the fee drag to paper-comparable levels, position size would need to be ~10× larger.
2. **ETP/ETH-PERP basis (net ~+1.4% in this period, unreliable)** — the two instruments occasionally diverge, causing extra or missed signals. Helped on Apr 16, hurt on Apr 17.
3. **First trade late/bad fill (−0.62%)** — bot startup execution issue.
4. **One slippage event (−0.50%)** — market timeout on Apr 11 LONG exit.
