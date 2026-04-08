"""Fetch OHLC candles from Kraken Futures public API."""
import urllib.request
import json
import time

KRAKEN_OHLC_URL = "https://futures.kraken.com/api/charts/v1/trade/{symbol}/{interval}"

# Kraken API accepts string intervals, not minutes
_INTERVAL_MAP = {
    1: "1m", 5: "5m", 15: "15m", 30: "30m",
    60: "1h", 240: "4h", 720: "12h", 1440: "1d",
}

def _fetch_page(symbol: str, interval_str: str, to_ts: int = None) -> list[dict]:
    """Fetch one page of candles (max ~2000). to_ts in milliseconds."""
    url = KRAKEN_OHLC_URL.format(symbol=symbol, interval=interval_str)
    if to_ts:
        url += f"?to={to_ts}"
    req = urllib.request.Request(url, headers={"User-Agent": "darwin/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    candles = []
    for c in data.get("candles", []):
        candles.append({
            "timestamp": c["time"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c.get("volume", 0)),
        })
    return candles


def fetch_ohlc(symbol: str = "PF_SOLUSD", interval: int = 60, count: int = 2160) -> list[dict]:
    """Fetch OHLC candles with pagination. interval in minutes.
    Paginates backward to get up to `count` candles."""
    interval_str = _INTERVAL_MAP.get(interval, "1h")

    all_candles = []
    to_ts = None  # start from most recent

    while len(all_candles) < count:
        page = _fetch_page(symbol, interval_str, to_ts)
        if not page:
            break

        # Deduplicate and prepend (pages go backward)
        existing_ts = {c["timestamp"] for c in all_candles}
        new_candles = [c for c in page if c["timestamp"] not in existing_ts]
        if not new_candles:
            break

        all_candles = sorted(new_candles + all_candles, key=lambda c: c["timestamp"])

        # Next page: fetch candles before the oldest we have
        oldest_ts = all_candles[0]["timestamp"]
        to_ts = oldest_ts
        time.sleep(0.3)  # rate limit

    return all_candles[-count:]


def fetch_multi(symbols: list[str] = None, interval: int = 60, count: int = 2160) -> dict[str, list[dict]]:
    """Fetch OHLC for multiple symbols."""
    symbols = symbols or ["PF_SOLUSD", "PF_DOTUSD", "PF_ADAUSD"]
    return {s: fetch_ohlc(s, interval, count) for s in symbols}
