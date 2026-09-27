"""BTC Turtle Aggressive
Trades Bybit BTC/USDT perpetual on 4h bars, long and short (one-way position mode).
Entry: Donchian breakout of the last entry_period bars, optionally in the direction of the
trend EMA. Pyramiding: add one unit every add_step_atr x ATR of favorable move, up to
max_units; the stop trails to atr_stop_mult x ATR behind the latest add. Never adds to losers.
Exit: opposite exit_period channel break or the ATR stop.
Risk: risk_pct is the equity risked by a fully pyramided position (measured from the entry
stop), always a fraction of current equity, so size compounds as the account grows.
Drawdown tiers cut risk to 50% / 25% and halt new entries at dd_halt from the equity peak.
Leverage: set the panel_leverage param to the same value chosen in the backtest / deployment panel.
Total notional is capped at leverage x equity (minus a small buffer for fees).
"""

PERSIST_RUNTIME_STATE = True

# @param enable_long bool true Allow long entries
# @param enable_short bool true Allow short entries
# @param entry_period int 20 Breakout channel length range=5:120:1
# @param exit_period int 10 Exit channel length range=2:60:1
# @param atr_period int 20 ATR period range=5:60:1
# @param atr_stop_mult float 2.0 Stop distance in ATR range=0.5:6.0:0.25
# @param use_trend_filter bool true Only trade in the direction of the trend EMA
# @param trend_period int 200 Trend EMA period range=50:300:10
# @param panel_leverage float 1 Must equal the leverage chosen in the backtest or deployment panel range=1:20:1
# @param risk_pct float 0.03 Equity risked by a full position range=0.001:1.0:0.001
# @param max_units int 3 Maximum pyramid units range=1:50:1
# @param add_step_atr float 0.5 Favorable move in ATR before adding a unit range=0.25:2.0:0.25
# @param profit_boost float 0.0 Extra risk per 100% equity growth, 0 is plain compounding range=0.0:5.0:0.1
# @param dd_level1 float 0.10 Drawdown where risk is halved range=0.03:0.30:0.01
# @param dd_level2 float 0.18 Drawdown where risk is quartered range=0.05:0.40:0.01
# @param dd_halt float 0.25 Drawdown where new entries stop range=0.10:0.60:0.01

TIMEFRAME = "4h"
# Keep a little headroom so fees and slippage do not push the order over the margin limit.
NOTIONAL_BUFFER = 0.95


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@bybit:swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency=TIMEFRAME)
    context.set_metadata(direction_mode="one_way")
    # Highest leverage the panel may select. Keep it a plain number so the panel can detect it.
    context.allow_leverage(max_leverage=20)
    context.set_warmup(400)
    g.side = 0
    g.units = 0
    g.unit_weight = 0.0
    g.last_add_price = 0.0
    g.n = 0.0
    g.peak_equity = 0.0
    g.halted = False
    g.leverage = 1.0


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
        "entry_period": _param(context, "entry_period", 20),
        "exit_period": _param(context, "exit_period", 10),
        "atr_period": _param(context, "atr_period", 20),
        "atr_stop_mult": _param(context, "atr_stop_mult", 2.0),
        "use_trend_filter": _param(context, "use_trend_filter", True),
        "trend_period": _param(context, "trend_period", 200),
        "risk_pct": _param(context, "risk_pct", 0.03),
        "max_units": _param(context, "max_units", 3),
        "add_step_atr": _param(context, "add_step_atr", 0.5),
        "panel_leverage": max(1.0, _param(context, "panel_leverage", 1.0)),
        "profit_boost": _param(context, "profit_boost", 0.0),
        "dd_level1": _param(context, "dd_level1", 0.10),
        "dd_level2": _param(context, "dd_level2", 0.18),
        "dd_halt": _param(context, "dd_halt", 0.25),
    }


def _reset_state():
    g.side = 0
    g.units = 0
    g.unit_weight = 0.0
    g.last_add_price = 0.0
    g.n = 0.0


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

    if drawdown >= p["dd_level2"]:
        risk *= 0.25
    elif drawdown >= p["dd_level1"]:
        risk *= 0.5
    return risk, drawdown


def _submit(target_notional, side, reason, stop_loss_pct=None):
    # order_target_percent is multiplied by the panel leverage, so convert notional to margin.
    percent = target_notional * side / g.leverage
    if stop_loss_pct is None:
        order_target_percent(g.symbol, percent, reason=reason)
    else:
        order_target_percent(g.symbol, percent, reason=reason, stop_loss_pct=stop_loss_pct)


def handle_data(context, data):
    p = _load_params(context)
    g.leverage = p["panel_leverage"]
    max_notional = p["panel_leverage"] * NOTIONAL_BUFFER

    required = max(p["entry_period"], p["exit_period"]) + 2
    required = max(required, p["atr_period"] * 3)
    if p["use_trend_filter"]:
        required = max(required, p["trend_period"] * 2)
    bars = get_history(required, TIMEFRAME, ["high", "low", "close"], g.symbol)
    if len(bars) < required - 1:
        return

    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    close_series = bars["close"].astype(float)
    close = float(close_series.iloc[-1])

    prev_close = close_series.shift(1)
    range_hl = high - low
    range_hc = (high - prev_close).abs()
    range_lc = (low - prev_close).abs()
    true_range = range_hl.where(range_hl >= range_hc, range_hc)
    true_range = true_range.where(true_range >= range_lc, range_lc)
    atr = float(true_range.ewm(alpha=1.0 / p["atr_period"], adjust=False).mean().iloc[-1])
    if atr <= 0 or close <= 0:
        return

    entry_high = float(high.iloc[-p["entry_period"] - 1:-1].max())
    entry_low = float(low.iloc[-p["entry_period"] - 1:-1].min())
    exit_high = float(high.iloc[-p["exit_period"] - 1:-1].max())
    exit_low = float(low.iloc[-p["exit_period"] - 1:-1].min())

    trend_up = True
    trend_down = True
    if p["use_trend_filter"]:
        ema = float(close_series.ewm(span=p["trend_period"], adjust=False).mean().iloc[-1])
        trend_up = close > ema
        trend_down = close < ema

    position = get_position(g.symbol)
    amount = float(position.amount or 0.0)
    side = 1 if amount > 0 else (-1 if amount < 0 else 0)

    # Keep strategy state consistent with the synced position.
    if side == 0 and g.units > 0:
        _reset_state()
    elif side != 0 and (g.side != side or g.units <= 0):
        g.side = side
        g.units = 1
        g.last_add_price = float(position.avg_cost or close)
        g.n = atr
        g.unit_weight = min(abs(amount) * close / max(float(context.portfolio.total_value), 1e-9), max_notional)

    risk, drawdown = _risk_fraction(context, p)

    if side != 0:
        stop_price = g.last_add_price - side * p["atr_stop_mult"] * g.n
        channel_exit = close < exit_low if side > 0 else close > exit_high
        stop_hit = close < stop_price if side > 0 else close > stop_price
        if channel_exit or stop_hit:
            _submit(0.0, 1, "turtle_exit_channel" if channel_exit else "turtle_exit_stop")
            _reset_state()
            return

        add_trigger = g.last_add_price + side * p["add_step_atr"] * g.n
        can_add = close >= add_trigger if side > 0 else close <= add_trigger
        if can_add and g.units < p["max_units"] and not g.halted and risk > 0:
            target = min((g.units + 1) * g.unit_weight, max_notional)
            if target <= g.units * g.unit_weight + 1e-9:
                return
            g.units += 1
            g.last_add_price = close
            new_stop = close - side * p["atr_stop_mult"] * g.n
            log.info("Add unit %d at %.2f, stop %.2f" % (g.units, close, new_stop))
            _submit(
                target,
                side,
                "turtle_add_long" if side > 0 else "turtle_add_short",
                stop_loss_pct=abs(close - new_stop) / close,
            )
        return

    if g.halted or risk <= 0:
        return

    go_long = p["enable_long"] and close > entry_high and trend_up
    go_short = p["enable_short"] and close < entry_low and trend_down
    if not (go_long or go_short):
        return
    direction = 1 if go_long else -1

    stop_dist = p["atr_stop_mult"] * atr
    stop_pct = stop_dist / close
    # Size each unit so that a fully pyramided position risks about `risk` of equity.
    full_weight = min(risk / stop_pct, max_notional)
    unit_weight = full_weight / max(1, p["max_units"])
    if unit_weight <= 0:
        return

    g.side = direction
    g.units = 1
    g.unit_weight = unit_weight
    g.last_add_price = close
    g.n = atr
    log.info(
        "Enter %s at %.2f: risk=%.2f%% dd=%.1f%% notional=%.2fx leverage=%.0fx stop=%.2f"
        % (
            "long" if direction > 0 else "short",
            close,
            risk * 100,
            drawdown * 100,
            unit_weight,
            p["panel_leverage"],
            close - direction * stop_dist,
        )
    )
    _submit(
        unit_weight,
        direction,
        "turtle_breakout_long" if direction > 0 else "turtle_breakout_short",
        stop_loss_pct=stop_pct,
    )
