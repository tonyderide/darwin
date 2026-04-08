"""Technical indicators — pre-computed on candle arrays."""
import math


def ema(prices: list[float], period: int) -> list[float | None]:
    if len(prices) < period:
        return [None] * len(prices)
    result = [None] * (period - 1)
    sma = sum(prices[:period]) / period
    result.append(sma)
    k = 2 / (period + 1)
    for i in range(period, len(prices)):
        result.append(prices[i] * k + result[-1] * (1 - k))
    return result


def rsi(prices: list[float], period: int = 14) -> list[float | None]:
    if len(prices) < period + 1:
        return [None] * len(prices)
    result = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period

    def _val(ag, al):
        return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

    result.append(_val(avg_g, avg_l))
    for i in range(period + 1, len(prices)):
        d = prices[i] - prices[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        result.append(_val(avg_g, avg_l))
    return result


def bollinger(prices: list[float], period: int = 20, num_std: float = 2.0):
    """Returns (upper, mid, lower, bandwidth) arrays."""
    n = len(prices)
    upper = [None] * n
    mid = [None] * n
    lower = [None] * n
    bw = [None] * n
    for i in range(period - 1, n):
        window = prices[i - period + 1: i + 1]
        m = sum(window) / period
        std = math.sqrt(sum((x - m) ** 2 for x in window) / period)
        mid[i] = m
        upper[i] = m + num_std * std
        lower[i] = m - num_std * std
        if m > 0:
            bw[i] = (upper[i] - lower[i]) / m * 100
    return upper, mid, lower, bw


def adx(candles: list[dict], period: int = 14) -> list[float | None]:
    """Average Directional Index."""
    n = len(candles)
    if n < period + 1:
        return [None] * n

    result = [None] * n
    tr_list, plus_dm_list, minus_dm_list = [], [], []

    for i in range(1, n):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        prev_h = candles[i - 1]["high"]
        prev_l = candles[i - 1]["low"]

        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        plus_dm = max(h - prev_h, 0) if (h - prev_h) > (prev_l - l) else 0
        minus_dm = max(prev_l - l, 0) if (prev_l - l) > (h - prev_h) else 0

        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return result

    # Smoothed TR, +DM, -DM
    atr = sum(tr_list[:period])
    a_plus = sum(plus_dm_list[:period])
    a_minus = sum(minus_dm_list[:period])

    dx_list = []
    for i in range(period - 1, len(tr_list)):
        if i == period - 1:
            pass  # use initial sums
        else:
            atr = atr - atr / period + tr_list[i]
            a_plus = a_plus - a_plus / period + plus_dm_list[i]
            a_minus = a_minus - a_minus / period + minus_dm_list[i]

        plus_di = (a_plus / atr * 100) if atr > 0 else 0
        minus_di = (a_minus / atr * 100) if atr > 0 else 0
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
        dx_list.append(dx)

    if len(dx_list) < period:
        return result

    # Smooth DX into ADX
    adx_val = sum(dx_list[:period]) / period
    offset = period  # first valid ADX at index period + period - 1 in candles
    result[offset + period - 1] = adx_val
    for i in range(period, len(dx_list)):
        adx_val = (adx_val * (period - 1) + dx_list[i]) / period
        result[offset + i] = adx_val

    return result


def enrich_candles(candles: list[dict]) -> list[dict]:
    """Add indicator values to each candle dict. Mutates in-place."""
    closes = [c["close"] for c in candles]

    ema8 = ema(closes, 8)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes, 14)
    bb_upper, bb_mid, bb_lower, bb_bw = bollinger(closes, 20, 2.0)
    adx14 = adx(candles, 14)

    # BB squeeze: bandwidth in bottom 25th percentile (lookback 100)
    for i, c in enumerate(candles):
        c["ema8"] = ema8[i]
        c["ema21"] = ema21[i]
        c["ema50"] = ema50[i]
        c["ema200"] = ema200[i]
        c["rsi"] = rsi14[i]
        c["bb_upper"] = bb_upper[i]
        c["bb_mid"] = bb_mid[i]
        c["bb_lower"] = bb_lower[i]
        c["bb_bw"] = bb_bw[i]
        c["adx"] = adx14[i]

        # BB squeeze detection
        if bb_bw[i] is not None:
            lookback = [bb_bw[j] for j in range(max(0, i - 100), i + 1) if bb_bw[j] is not None]
            if len(lookback) >= 10:
                p25 = sorted(lookback)[int(len(lookback) * 0.25)]
                c["bb_squeeze"] = bb_bw[i] <= p25
            else:
                c["bb_squeeze"] = False
        else:
            c["bb_squeeze"] = False

    return candles
