# Design: Pre-Trade News/Catalyst Gate + Data-Driven Entry Filters (Whale v2)

**Date:** 2026-06-09
**Status:** Draft — awaiting review

## Motivation

Analysis of the first **27 closed whale trades** (the v1 baseline) revealed the strategy's
core weaknesses:

- **The price signal is not predictive.** Losers had a *higher* average thrust (+8.2%)
  than winners (+6.4%), and similar volume. Volume+thrust alone cannot separate winners
  from losers.
- **Profit is outlier-dependent.** Net +22.5% gross, but +56.8% of that came from 3 trades
  (FTT, MOVE, USTC). Without them the other 24 trades net **−34.3%**.
- **Inverted risk/reward.** Typical win +2–4% (decaying TP books early); every loss is the
  full −7% stop. Needs ~70% win rate to profit; actual is 52%.
- **Two loss archetypes:** (a) **blow-off reversals** — the most extreme spikes died
  fastest (DEGO +19.8% thrust dead in 11m, RAD 32.4× volume dead in 37m); (b) **weak
  faders** — signals right at the 3.4–3.9× volume floor bled to the stop.
- **Already-pumped coins lost** (DASH −7.0%, RIF −8.1%) — the predicted "RIF/DASH scenario."

**Conclusion:** the entry needs *outside information* (news/catalysts) the price signal
lacks, plus three cheap filters that target the demonstrated loss archetypes.

## v1 Baseline Scorecard (for comparison after v2)

- 27 closed whale trades, **52% win rate**
- avg win **+7.03%**, avg loss **−7.22%**
- net **+22.5% gross** (no fees); **−34.3% without the top 3 outliers**

## Goal

Add an automated, free pre-trade gate that runs **only on candidates about to open**
(a handful per scan), combining a Gemini-grounded news/catalyst read with cheap bot-side
filters, to skip the loss archetypes and add predictive signal — then reset and measure
v2 against the baseline.

## Design Overview

At the final pre-open step (whale detected + existing filters passed + `_can_open()`),
run checks in **cost order** (cheap/free first, Gemini last):

1. **Already-pumped check (free, bot-side):** if the coin is up ≥ `pumped_skip_pct` (30%)
   over 7 days → **skip**.
2. **Multi-candle blow-off cap (free, in `detect_whale`):** if the thrust over the lookback
   window ≥ `whale_max_thrust_pct` (≈18%) → reject (exhaustion top).
3. **Higher volume floor (free, config):** raise `whale_volume_multiple` 3.0 → `5.0`.
4. **Gemini grounded catalyst classifier (free tier, ~3–5s):** one search-grounded call,
   recency-constrained to 48h. Returns `{catalyst, sentiment, latest_date, reason}`.
   - `sentiment < news_veto_threshold` (35) → **skip** (bearish news).
   - `catalyst == migration` → open at **reduced size** (`migration_size_multiplier`, 0.5).
   - `listing | partnership | launch` → allow (positive catalyst).
   - no recent news → neutral, allow (rely on whale/technical quality).

For **spot** signals, the same Gemini sentiment is **blended back into the total score**
(re-enabling the 65/35 technical/news weighting that is currently disabled because the old
news pipeline is dead), so bearish news can pull a tech-strong coin below threshold.

## Components

### 1. `backend/news.py` — `grounded_catalyst(symbol, name)`
- One call: `gemini-2.5-flash` with `google_search` grounding (verified working free).
- Prompt constrains to **news from the last 48h**, asks for the latest date, classifies the
  catalyst, and scores sentiment 0–100 (50 = neutral / no recent news).
- Returns a `CatalystResult(sentiment: float, catalyst: str, latest_date: str, reason: str,
  analyzed: bool)`.
- **Fail-safe:** any error / rate-limit / no-recent-news → `analyzed=False`, sentiment 50,
  catalyst `none` (never blocks a trade on an API hiccup).
- Replaces the dead CMC-headline → Gemini path for the entry gate.

### 2. Already-pumped check — `backend/gecko.py`
- Extend the CoinGecko markets lookup to also return **7-day price change**
  (`price_change_percentage=7d` param on `/coins/markets`). One field, no extra call.
- Scanner skips the candidate if `change_7d >= pumped_skip_pct`.

### 3. Multi-candle blow-off cap — `backend/whale_strategy.py`
- In `detect_whale`, after computing `price_thrust_pct`, reject if it is
  `>= cfg.whale_max_thrust_pct` (parabolic over the 3-candle window, distinct from the
  existing single-candle blow-off guard).

### 4. Volume floor — `backend/config.yaml`
- `whale.volume_multiple: 3.0 → 5.0` (configurable; cuts the 3.4× faders).

### 5. Variable position size — `backend/paper_trading.py`
- `open_position(..., size_multiplier: float = 1.0)` scales the notional. Used to half-size
  migration-risk coins.

### 6. Scanner integration — `backend/scanner.py`
- `_open_whale`: run checks 1–4 in order; veto/skip/half-size per the rules above.
- `_scan_coin` (spot): call `grounded_catalyst` for firing candidates and blend
  `sentiment` into `compute_total_score` (news weight re-enabled).

### 7. Config additions (`config.yaml` + `config.py`)
- `scoring.pumped_skip_pct: 30`
- `scoring.news_veto_threshold: 35`
- `whale.max_thrust_pct: 18`
- `whale.volume_multiple: 5.0` (raised)
- `paper_trading.migration_size_multiplier: 0.5`
- Re-enable news weighting for spot (already has `news_weight: 0.35`).

## Data Flow

```
candidate (whale detected, filters pass, can_open)
  → 7d-pump check (gecko)            [skip if ≥30%]
  → blow-off / volume floor          [in detect_whale, already filtered]
  → grounded_catalyst (Gemini)       [skip if bearish; half-size if migration]
  → open_position(size_multiplier)
```

## Free-Tier & Error Handling

- Gemini called **only for candidates about to open** (≤ a few per scan), never the full
  ~2,785-coin universe — keeps it inside the free tier.
- Every external call (Gemini, CoinGecko 7d) fails safe to **neutral/allow**, never blocks
  or crashes a scan.
- Grounded call latency (~3–5s) applies only to the few firing candidates.

## Testing

- `news.grounded_catalyst`: mock the Gemini client; assert structured parsing + neutral
  fallback on error.
- `whale_strategy`: blow-off cap rejects a ≥18% thrust.
- `scanner`: candidate is skipped when 7d-change ≥ 30%; whale vetoed on bearish sentiment;
  migration → half size; spot blends sentiment into the score.
- All existing 85 tests stay green.

## Rollout

1. Record v1 baseline scorecard (above).
2. Implement v2, all tests green.
3. **Reset the DB** (wipe positions/signals) so v2 stats are clean.
4. Restart; collect ~25 closed trades; compare win rate / expectancy to baseline.

## Out of Scope (future)

- Trailing stop / scale-out for runners (separate experiment).
- ATR-based stops.
- Applying the news gate to all scored coins (cost-prohibitive on the free tier).
- The manual "ask Claude" MCP tool (dropped in favour of the automated gate).
