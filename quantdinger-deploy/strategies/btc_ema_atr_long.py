"""BTC Dual EMA ATR Long
Trades Bybit BTC/USDT perpetual on 15m bars, long only.
Signal: enter long when EMA20 crosses above EMA50; exit when EMA20 falls below EMA50.
Risk: each entry risks risk_pct of equity; stop-loss is atr_mult x ATR below entry.
Position size grows and shrinks with account equity (compounding), capped at max_weight.
No leverage, no averaging down, one position at a time.
"""

# @param fast_period int 20 Fast EMA period range=5:60:5
# @param slow_period int 50 Slow EMA period range=20:200:10
# @param atr_period int 14 ATR period range=5:50:1
# @param atr_mult float 1.5 Stop distance in ATR multiples range=0.5:5.0:0.5
# @param risk_pct float 0.01 Equity risked per trade range=0.0025:0.03:0.0025
# @param max_weight float 0.95 Maximum position weight of equity range=0.1:1.0:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@bybit:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="15m")
    context.set_warmup(300)
    context.set_metadata(direction_mode="long_only")


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 20))
    slow_period = int(context.params.get("slow_period", 50))
    atr_period = int(context.params.get("atr_period", 14))
    atr_mult = float(context.params.get("atr_mult", 1.5))
    risk_pct = float(context.params.get("risk_pct", 0.01))
    max_weight = float(context.params.get("max_weight", 0.95))

    if fast_period >= slow_period:
        log.warning("fast_period must be smaller than slow_period")
        return

    lookback = max(slow_period, atr_period) * 3
    bars = get_history(lookback, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < max(slow_period, atr_period) + 2:
        return

    close = bars["close"]
    high = bars["high"]
    low = bars["low"]

    fast = close.ewm(span=fast_period, adjust=False).mean()
    slow = close.ewm(span=slow_period, adjust=False).mean()
    fast_now = float(fast.iloc[-1])
    slow_now = float(slow.iloc[-1])
    fast_prev = float(fast.iloc[-2])
    slow_prev = float(slow.iloc[-2])

    prev_close = close.shift(1)
    range_hl = high - low
    range_hc = (high - prev_close).abs()
    range_lc = (low - prev_close).abs()
    true_range = range_hl.where(range_hl >= range_hc, range_hc)
    true_range = true_range.where(true_range >= range_lc, range_lc)
    atr = float(true_range.rolling(atr_period).mean().iloc[-1])
    price = float(close.iloc[-1])
    if atr <= 0 or price <= 0:
        return

    position = get_position(g.symbol)
    has_long = float(position.amount or 0.0) > 0

    crossed_up = fast_prev <= slow_prev and fast_now > slow_now

    if crossed_up and not has_long:
        stop_distance = atr_mult * atr
        stop_loss_pct = stop_distance / price
        # Size so that hitting the stop loses about risk_pct of equity.
        weight = min(risk_pct / stop_loss_pct, max_weight)
        order_target_percent(
            g.symbol,
            weight,
            reason="ema_cross_long_entry",
            stop_loss_pct=stop_loss_pct,
        )
    elif has_long and fast_now < slow_now:
        order_target_percent(
            g.symbol,
            0.0,
            reason="ema_cross_long_exit",
        )
