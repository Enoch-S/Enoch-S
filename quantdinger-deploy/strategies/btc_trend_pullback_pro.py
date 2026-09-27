"""BTC Trend Pullback Pro
Trades Bybit BTC/USDT perpetual on 1h bars, long and short (one-way position mode).
Signal: trade pullbacks in the direction of the EMA trend. Long when price and the fast EMA
are above the trend EMA and RSI recovers up through rsi_long_level; short is the mirror.
Validation: every new signal is replayed over the last validation_bars bars with the same
stop-loss / take-profit. An entry is taken only when that setup's historical win rate is at
least min_win_rate over at least min_samples trades AND its expectancy after costs is positive.
Risk: fixed-fractional sizing (risk_pct of current equity per trade) so size compounds with
equity; optional profit_boost raises risk as equity grows, capped at max_risk_pct.
Drawdown tiers cut risk to 50% / 25% and halt new entries at dd_halt from the equity peak.
Exits: user-defined stop-loss / take-profit (ATR or percent), optional trailing stop,
time limit, and exit on an opposite signal. No leverage, no averaging down.
"""

import numpy as np

PERSIST_RUNTIME_STATE = True

# @param enable_long bool true Allow long entries
# @param enable_short bool true Allow short entries
# @param fast_period int 50 Fast EMA period range=10:100:5
# @param trend_period int 200 Trend EMA period range=100:300:10
# @param rsi_period int 14 RSI period range=5:30:1
# @param rsi_long_level float 40 RSI level a long pullback must recover through range=25:50:1
# @param rsi_short_level float 60 RSI level a short pullback must fall through range=50:75:1
# @param atr_period int 14 ATR period range=5:50:1
# @param use_atr_stops bool true Use ATR multiples for stops (false uses fixed percentages)
# @param sl_atr float 2.0 Stop-loss distance in ATR range=0.5:6.0:0.25
# @param tp_atr float 1.5 Take-profit distance in ATR range=0.5:8.0:0.25
# @param sl_pct float 0.02 Stop-loss percent when ATR stops are off range=0.005:0.10:0.005
# @param tp_pct float 0.015 Take-profit percent when ATR stops are off range=0.005:0.20:0.005
# @param trailing_stop_pct float 0.0 Trailing stop percent, 0 disables range=0.0:0.10:0.005
# @param max_hold_bars int 48 Close a trade after this many 1h bars range=6:240:6
# @param cooldown_bars int 6 Bars to wait after a position closes range=0:48:1
# @param validation_bars int 1500 History bars used to validate a signal range=500:1500:100
# @param min_samples int 20 Minimum historical signals required range=10:100:5
# @param min_win_rate float 0.65 Minimum historical win rate range=0.50:0.90:0.01
# @param min_expectancy float 0.05 Minimum expectancy after costs in R units range=0.0:1.0:0.05
# @param cost_rate float 0.002 Round-trip fee plus slippage as a fraction of price range=0.0:0.01:0.0005
# @param risk_pct float 0.01 Equity risked per trade range=0.0025:0.03:0.0025
# @param max_risk_pct float 0.02 Hard cap on risk per trade range=0.005:0.05:0.005
# @param profit_boost float 0.0 Extra risk per 100% equity growth, 0 is plain compounding range=0.0:1.0:0.1
# @param max_weight float 1.0 Maximum position notional as a fraction of equity range=0.1:1.0:0.05
# @param dd_level1 float 0.05 Drawdown where risk is halved range=0.02:0.20:0.01
# @param dd_level2 float 0.10 Drawdown where risk is quartered range=0.04:0.30:0.01
# @param dd_halt float 0.20 Drawdown where new entries stop range=0.05:0.50:0.01

TIMEFRAME = "1h"
BAR_SECONDS = 3600


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@bybit:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency=TIMEFRAME)
    context.set_warmup(1500)
    context.set_metadata(direction_mode="one_way")
    g.bar_count = 0
    g.last_exit_bar = -100000
    g.prev_side = 0
    g.peak_equity = 0.0
    g.halted = False


def _param(context, name, default):
    value = context.params.get(name, default)
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int):
        return int(value)
    return float(value)


def _load_params(context):
    return {
        "enable_long": _param(context, "enable_long", True),
        "enable_short": _param(context, "enable_short", True),
        "fast_period": _param(context, "fast_period", 50),
        "trend_period": _param(context, "trend_period", 200),
        "rsi_period": _param(context, "rsi_period", 14),
        "rsi_long_level": _param(context, "rsi_long_level", 40.0),
        "rsi_short_level": _param(context, "rsi_short_level", 60.0),
        "atr_period": _param(context, "atr_period", 14),
        "use_atr_stops": _param(context, "use_atr_stops", True),
        "sl_atr": _param(context, "sl_atr", 2.0),
        "tp_atr": _param(context, "tp_atr", 1.5),
        "sl_pct": _param(context, "sl_pct", 0.02),
        "tp_pct": _param(context, "tp_pct", 0.015),
        "trailing_stop_pct": _param(context, "trailing_stop_pct", 0.0),
        "max_hold_bars": _param(context, "max_hold_bars", 48),
        "cooldown_bars": _param(context, "cooldown_bars", 6),
        "validation_bars": _param(context, "validation_bars", 1500),
        "min_samples": _param(context, "min_samples", 20),
        "min_win_rate": _param(context, "min_win_rate", 0.65),
        "min_expectancy": _param(context, "min_expectancy", 0.05),
        "cost_rate": _param(context, "cost_rate", 0.002),
        "risk_pct": _param(context, "risk_pct", 0.01),
        "max_risk_pct": _param(context, "max_risk_pct", 0.02),
        "profit_boost": _param(context, "profit_boost", 0.0),
        "max_weight": _param(context, "max_weight", 1.0),
        "dd_level1": _param(context, "dd_level1", 0.05),
        "dd_level2": _param(context, "dd_level2", 0.10),
        "dd_halt": _param(context, "dd_halt", 0.20),
    }


def _indicators(bars, p):
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)

    ema_fast = close.ewm(span=p["fast_period"], adjust=False).mean()
    ema_trend = close.ewm(span=p["trend_period"], adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / p["rsi_period"], adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / p["rsi_period"], adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi = (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)

    prev_close = close.shift(1)
    range_hl = high - low
    range_hc = (high - prev_close).abs()
    range_lc = (low - prev_close).abs()
    true_range = range_hl.where(range_hl >= range_hc, range_hc)
    true_range = true_range.where(true_range >= range_lc, range_lc)
    atr = true_range.ewm(alpha=1.0 / p["atr_period"], adjust=False).mean()

    return {
        "open": bars["open"].astype(float).to_numpy(),
        "high": high.to_numpy(),
        "low": low.to_numpy(),
        "close": close.to_numpy(),
        "ema_fast": ema_fast.to_numpy(),
        "ema_trend": ema_trend.to_numpy(),
        "rsi": rsi.to_numpy(),
        "atr": atr.to_numpy(),
    }


def _signals(ind, p):
    """Boolean arrays marking bars where a long or short setup completes."""
    close = ind["close"]
    ema_fast = ind["ema_fast"]
    ema_trend = ind["ema_trend"]
    rsi = ind["rsi"]
    rsi_prev = np.roll(rsi, 1)
    rsi_prev[0] = rsi[0]

    long_sig = (
        (close > ema_trend)
        & (ema_fast > ema_trend)
        & (rsi_prev < p["rsi_long_level"])
        & (rsi >= p["rsi_long_level"])
    )
    short_sig = (
        (close < ema_trend)
        & (ema_fast < ema_trend)
        & (rsi_prev > p["rsi_short_level"])
        & (rsi <= p["rsi_short_level"])
    )
    warm = max(p["trend_period"], p["atr_period"], p["rsi_period"]) * 2
    long_sig[:warm] = False
    short_sig[:warm] = False
    return long_sig, short_sig


def _stop_distances(price, atr_value, p):
    if p["use_atr_stops"]:
        return p["sl_atr"] * atr_value, p["tp_atr"] * atr_value
    return p["sl_pct"] * price, p["tp_pct"] * price


def _validate(ind, signal_mask, direction, p):
    """Replay past signals with the same exits. Returns (samples, win_rate, expectancy_r)."""
    opens = ind["open"]
    highs = ind["high"]
    lows = ind["low"]
    atrs = ind["atr"]
    last = len(opens) - 1
    horizon = p["max_hold_bars"]

    wins = 0
    losses = 0
    reward_r_total = 0.0
    cost_r_total = 0.0
    # Exclude the current bar: its outcome is unknown.
    for j in np.nonzero(signal_mask[:last])[0]:
        entry_bar = j + 1
        entry = opens[entry_bar]
        sl_dist, tp_dist = _stop_distances(entry, atrs[j], p)
        if sl_dist <= 0 or tp_dist <= 0 or entry <= 0:
            continue
        outcome = 0
        end_bar = min(entry_bar + horizon, last + 1)
        for k in range(entry_bar, end_bar):
            if direction > 0:
                # Conservative: when both are touched in one bar, count the stop first.
                if lows[k] <= entry - sl_dist:
                    outcome = -1
                    break
                if highs[k] >= entry + tp_dist:
                    outcome = 1
                    break
            else:
                if highs[k] >= entry + sl_dist:
                    outcome = -1
                    break
                if lows[k] <= entry - tp_dist:
                    outcome = 1
                    break
        if outcome == 0:
            if entry_bar + horizon > last:
                continue  # still unresolved at the current bar, skip
            outcome = -1  # timed out without reaching the target counts as a loss
        if outcome > 0:
            wins += 1
            reward_r_total += tp_dist / sl_dist
        else:
            losses += 1
        cost_r_total += p["cost_rate"] * entry / sl_dist

    samples = wins + losses
    if samples == 0:
        return 0, 0.0, 0.0
    win_rate = wins / samples
    expectancy_r = (reward_r_total - losses - cost_r_total) / samples
    return samples, win_rate, expectancy_r


def _risk_fraction(context, p):
    equity = float(context.portfolio.total_value)
    start = float(context.portfolio.starting_cash or equity)
    if equity <= 0:
        return 0.0, 0.0

    if equity > g.peak_equity:
        g.peak_equity = equity
    drawdown = 1.0 - equity / g.peak_equity if g.peak_equity > 0 else 0.0

    if drawdown >= p["dd_halt"]:
        if not g.halted:
            log.warning("Drawdown %.2f%% reached halt level; new entries stopped" % (drawdown * 100))
        g.halted = True
        return 0.0, drawdown
    g.halted = False

    growth = max(0.0, equity / start - 1.0) if start > 0 else 0.0
    risk = p["risk_pct"] * (1.0 + p["profit_boost"] * growth)
    risk = min(risk, p["max_risk_pct"])

    if drawdown >= p["dd_level2"]:
        risk *= 0.25
    elif drawdown >= p["dd_level1"]:
        risk *= 0.5
    return risk, drawdown


def handle_data(context, data):
    p = _load_params(context)
    g.bar_count += 1

    if p["fast_period"] >= p["trend_period"]:
        log.warning("fast_period must be smaller than trend_period")
        return

    position = get_position(g.symbol)
    amount = float(position.amount or 0.0)
    side = 1 if amount > 0 else (-1 if amount < 0 else 0)
    if g.prev_side != 0 and side == 0:
        g.last_exit_bar = g.bar_count
    g.prev_side = side

    risk, drawdown = _risk_fraction(context, p)

    fields = ["open", "high", "low", "close"]
    short_window = p["trend_period"] * 3 + 5
    bars = get_history(short_window, TIMEFRAME, fields, g.symbol)
    if len(bars) < p["trend_period"] * 2 + 2:
        return
    ind = _indicators(bars, p)
    long_sig, short_sig = _signals(ind, p)
    long_now = bool(long_sig[-1]) and p["enable_long"]
    short_now = bool(short_sig[-1]) and p["enable_short"]

    # Exit on an opposite signal; the reverse entry waits until the position is flat.
    if side > 0 and short_now:
        order_target_percent(g.symbol, 0.0, reason="long_exit_opposite_signal")
        return
    if side < 0 and long_now:
        order_target_percent(g.symbol, 0.0, reason="short_exit_opposite_signal")
        return

    if side != 0 or not (long_now or short_now):
        return
    if g.halted or risk <= 0:
        return
    if g.bar_count - g.last_exit_bar < p["cooldown_bars"]:
        return

    direction = 1 if long_now else -1

    # Validate the setup on a longer history before trading it.
    long_window = p["validation_bars"] + p["trend_period"] * 3
    hist = get_history(long_window, TIMEFRAME, fields, g.symbol)
    if len(hist) < p["trend_period"] * 3 + p["min_samples"] * 10:
        return
    hist_ind = _indicators(hist, p)
    hist_long, hist_short = _signals(hist_ind, p)
    mask = hist_long if direction > 0 else hist_short
    samples, win_rate, expectancy_r = _validate(hist_ind, mask, direction, p)

    if samples < p["min_samples"]:
        log.info("Skip %s: only %d historical samples" % ("long" if direction > 0 else "short", samples))
        return
    if win_rate < p["min_win_rate"] or expectancy_r < p["min_expectancy"]:
        log.info(
            "Skip %s: win_rate=%.1f%% expectancy=%.2fR samples=%d"
            % ("long" if direction > 0 else "short", win_rate * 100, expectancy_r, samples)
        )
        return

    price = float(ind["close"][-1])
    sl_dist, tp_dist = _stop_distances(price, float(ind["atr"][-1]), p)
    if sl_dist <= 0 or tp_dist <= 0 or price <= 0:
        return
    stop_loss_pct = sl_dist / price
    take_profit_pct = tp_dist / price

    # Size so that hitting the stop loses about `risk` of current equity.
    weight = min(risk / stop_loss_pct, p["max_weight"])
    if weight <= 0:
        return

    log.info(
        "Enter %s: win_rate=%.1f%% expectancy=%.2fR samples=%d risk=%.2f%% dd=%.1f%% weight=%.2f"
        % (
            "long" if direction > 0 else "short",
            win_rate * 100,
            expectancy_r,
            samples,
            risk * 100,
            drawdown * 100,
            weight,
        )
    )
    order_target_percent(
        g.symbol,
        weight * direction,
        reason="trend_pullback_long_entry" if direction > 0 else "trend_pullback_short_entry",
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=p["trailing_stop_pct"],
        time_limit_seconds=p["max_hold_bars"] * BAR_SECONDS,
    )
