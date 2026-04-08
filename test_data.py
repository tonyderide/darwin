import pytest
from data import fetch_ohlc

def test_fetch_ohlc_returns_candles():
    candles = fetch_ohlc("PF_SOLUSD", interval=60, count=100)
    assert len(candles) > 0
    assert "open" in candles[0]
    assert "high" in candles[0]
    assert "low" in candles[0]
    assert "close" in candles[0]
    assert "timestamp" in candles[0]
