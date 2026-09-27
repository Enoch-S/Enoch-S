"""ETH Turtle Scalper 1m
Trades Bybit ETH/USDT perpetual on 1m bars, long and short (one-way position mode).
Entry: Donchian breakout of the last entry_period bars in the direction of the trend EMA.
Exit: opposite exit_period channel break, optional take-profit, max holding time, or the
hard stop. Stops and targets are expressed as a share of the position margin:
price move = margin share / leverage (5x leverage and a 40% margin stop = 8% price move).
Sizing: margin_pct of current equity is committed as margin for a full position, so size
compounds as the account grows. Optional pyramiding adds units only to winners.
Drawdown tiers cut size to 50% / 25% and pause new entries at dd_halt; after halt_cooldown
bars the peak resets and trading resumes.
Leverage: set panel_leverage to the same value chosen in the backtest / deployment panel.
"""

PERSIST_RUNTIME_STATE = True

# @param enable_long bool true Allow long entries
# @param enable_short bool true Allow short entries
# @param entry_period int 20 Breakout channel length in 1m bars range=5:240:1
# @param exit_period int 10 Exit channel length in 1m bars range=2:120:1
# @param use_trend_filter bool true Only trade in the direction of the trend EMA
# @param trend_period int 200 Trend EMA period in 1m bars range=50:600:10
# @param atr_period int 14 ATR period used for pyramiding steps range=5:60:1
# @param panel_leverage float 5 Must equal the leverage chosen in the backtest or deployment panel range=1:20:1
# @param margin_pct float 0.2 Share of equity used as margin for a full position range=0.01:1.0:0.01
# @param stop_margin_pct float 0.4 Stop when the position loses this share of its margin range=0.05:1.0:0.05
# @param tp_margin_pct float 0.0 Take profit at this share of margin, 0 disables range=0.0:3.0:0.05
# @param max_hold_bars int 240 Close a trade after this many 1m bars, 0 disables range=0:1440:10
# @param cooldown int 5 Bars to wait after a position closes range=0:120:1
# @param max_units int 1 Pyramid units, 1 disables pyramiding range=1:10:1
# @param add_step_atr float 1.0 Favorable move in ATR before adding a unit range=0.25:5.0:0.25
# @param profit_boost float 0.0 Extra size per 100% equity growth, 0 is plain compounding range=0.0:5.0:0.1
# @param dd_level1 float 0.15 Drawdown where size is halved range=0.03:0.50:0.01
# @param dd_level2 float 0.25 Drawdown where size is quartered range=0.05:0.60:0.01
# @param dd_halt float 0.35 Drawdown where new entries pause range=0.10:0.80:0.01
# @param halt_cooldown int 240 Bars to pause after dd_halt before trading resumes range=0:2880:10

TIMEFRAME = "1m"
BAR_SECONDS = 60
# Keep a little headroom so fees and slippage do not push the order over the margin limit.
MARGIN_BUFFER = 0.95


def initialize(context):
    g.symbol = "Crypto:ETH/USDT@bybit:swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency=TIMEFRAME)
    context.set_metadata(direction_mode="one_way")
    # Highest leverage the panel may select. Keep it a plain number so the panel can detect it.
    context.allow_leverage(max_leverage=20)
    context.set_warmup(1200)
    g.side = 0
    g.units = 0
    g.unit_margin = 0.0
    g.last_add_price = 0.0
    g.n = 0.0
    g.entry_bar = 0
    g.last_exit_bar = -100000
    g.peak_equity = 0.0
    g.halted = False
    g.halt_until = 0
    g.bar_count = 0


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
        "use_trend_filter": _param(context, "use_trend_filter", True),
        "trend_period": _param(context, "trend_period", 200),
        "atr_period": _param(context, "atr_period", 14),
        "panel_leverage": max(1.0, _param(context, "panel_leverage", 5.0)),
        "margin_pct": _param(context, "margin_pct", 0.2),
        "stop_margin_pct": _param(context, "stop_margin_pct", 0.4),
        "tp_margin_pct": _param(context, "tp_margin_pct", 0.0),
        "max_hold_bars": _param(context, "max_hold_bars", 240),
        "cooldown": _param(context, "cooldown", 5),
        "max_units": max(1, _param(context, "max_units", 1)),
        "add_step_atr": _param(context, "add_step_atr", 1.0),
        "profit_boost": _param(context, "profit_boost", 0.0),
        "dd_level1": _param(context, "dd_level1", 0.15),
        "dd_level2": _param(context, "dd_level2", 0.25),
        "dd_halt": _param(context, "dd_halt", 0.35),
        "halt_cooldown": _param(context, "halt_cooldown", 240),
    }


def _reset_state():
    g.side = 0
    g.units = 0
    g.unit_margin = 0.0
    g.last_add_price = 0.0
    g.n = 0.0
    g.entry_bar = 0


def _size_multiplier(context, p):
    """Return (multiplier on margin_pct, drawdown) after compounding and drawdown tiers."""
    equity = float(context.portfolio.total_value)
    start = float(context.portfolio.starting_cash or equity)
    if equity <= 0:
        return 0.0, 0.0

    if g.halted:
        if g.bar_count < g.halt_until:
            return 0.0, 1.0 - equity / g.peak_equity if g.peak_equity > 0 else 0.0
        # Cooldown finished: measure drawdown from here so trading can resume.
        log.info("Halt cooldown finished; drawdown peak reset to %.2f" % equity)
        g.halted = False
        g.peak_equity = equity

    if equity > g.peak_equity:
        g.peak_equity = equity
    drawdown = 1.0 - equity / g.peak_equity if g.peak_equity > 0 else 0.0

    if drawdown >= p["dd_halt"]:
        g.halted = True
        g.halt_until = g.bar_count + p["halt_cooldown"]
        log.warning(
            "Drawdown %.2f%% reached halt level; new entries paused for %d bars"
            % (drawdown * 100, p["halt_cooldown"])
        )
        return 0.0, drawdown

    growth = max(0.0, equity / start - 1.0) if start > 0 else 0.0
    multiplier = 1.0 + p["profit_boost"] * growth
    if drawdown >= p["dd_level2"]:
        multiplier *= 0.25
    elif drawdown >= p["dd_level1"]:
        multiplier *= 0.5
    return multiplier, drawdown


def _submit(margin_share, side, reason, stop_loss_pct=None, take_profit_pct=0.0, time_limit_seconds=0):
    # order_target_percent is a share of equity used as margin; the panel leverage multiplies it.
    options = {"reason": reason}
    if stop_loss_pct is not None:
        options["stop_loss_pct"] = stop_loss_pct
    if take_profit_pct > 0:
        options["take_profit_pct"] = take_profit_pct
    if time_limit_seconds > 0:
        options["time_limit_seconds"] = time_limit_seconds
    order_target_percent(g.symbol, margin_share * side, **options)


def handle_data(context, data):
    p = _load_params(context)
    g.bar_count += 1
    leverage = p["panel_leverage"]
    stop_pct = p["stop_margin_pct"] / leverage
    tp_pct = p["tp_margin_pct"] / leverage
    hold_seconds = p["max_hold_bars"] * BAR_SECONDS

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
    equity = max(float(context.portfolio.total_value), 1e-9)

    # Keep strategy state consistent with the synced position.
    if side == 0 and g.units > 0:
        _reset_state()
        g.last_exit_bar = g.bar_count
    elif side != 0 and (g.side != side or g.units <= 0):
        g.side = side
        g.units = 1
        g.last_add_price = float(position.avg_cost or close)
        g.n = atr
        g.entry_bar = g.bar_count
        g.unit_margin = min(abs(amount) * close / equity / leverage, MARGIN_BUFFER)

    multiplier, drawdown = _size_multiplier(context, p)

    if side != 0:
        avg_cost = float(position.avg_cost or g.last_add_price or close)
        move = (close - avg_cost) / avg_cost * side
        channel_exit = close < exit_low if side > 0 else close > exit_high
        stop_hit = move <= -stop_pct
        tp_hit = p["tp_margin_pct"] > 0 and move >= tp_pct
        time_hit = p["max_hold_bars"] > 0 and g.bar_count - g.entry_bar >= p["max_hold_bars"]
        if channel_exit or stop_hit or tp_hit or time_hit:
            if stop_hit:
                reason = "scalp_exit_stop"
            elif tp_hit:
                reason = "scalp_exit_target"
            elif time_hit:
                reason = "scalp_exit_time"
            else:
                reason = "scalp_exit_channel"
            _submit(0.0, 1, reason)
            _reset_state()
            g.last_exit_bar = g.bar_count
            return

        add_trigger = g.last_add_price + side * p["add_step_atr"] * g.n
        can_add = close >= add_trigger if side > 0 else close <= add_trigger
        if can_add and g.units < p["max_units"] and not g.halted and multiplier > 0:
            target = min((g.units + 1) * g.unit_margin, MARGIN_BUFFER)
            if target <= g.units * g.unit_margin + 1e-9:
                return
            g.units += 1
            g.last_add_price = close
            log.info("Add unit %d at %.2f" % (g.units, close))
            _submit(
                target,
                side,
                "scalp_add_long" if side > 0 else "scalp_add_short",
                stop_loss_pct=stop_pct,
                take_profit_pct=tp_pct,
            )
        return

    if g.halted or multiplier <= 0:
        return
    if g.bar_count - g.last_exit_bar < p["cooldown"]:
        return

    go_long = p["enable_long"] and close > entry_high and trend_up
    go_short = p["enable_short"] and close < entry_low and trend_down
    if not (go_long or go_short):
        return
    direction = 1 if go_long else -1

    full_margin = min(p["margin_pct"] * multiplier, MARGIN_BUFFER)
    unit_margin = full_margin / p["max_units"]
    if unit_margin <= 0:
        return

    g.side = direction
    g.units = 1
    g.unit_margin = unit_margin
    g.last_add_price = close
    g.n = atr
    g.entry_bar = g.bar_count
    log.info(
        "Enter %s at %.2f: margin=%.1f%% of equity, leverage=%.0fx, stop=%.2f%% price (%.0f%% margin), dd=%.1f%%"
        % (
            "long" if direction > 0 else "short",
            close,
            unit_margin * 100,
            leverage,
            stop_pct * 100,
            p["stop_margin_pct"] * 100,
            drawdown * 100,
        )
    )
    _submit(
        unit_margin,
        direction,
        "scalp_breakout_long" if direction > 0 else "scalp_breakout_short",
        stop_loss_pct=stop_pct,
        take_profit_pct=tp_pct,
        time_limit_seconds=hold_seconds,
    )
