import pytest
from agent import Agent
from arena import Arena

FAKE_CANDLES = [
    {"timestamp": i, "open": 100+i, "high": 102+i, "low": 99+i, "close": 100+i+0.5, "volume": 1000}
    for i in range(20)
]

def test_arena_evaluates_agents():
    a1 = Agent("a1", {"buy-on-dip-3pct": 0.8, "sell-on-pump-3pct": 0.7})
    a2 = Agent("a2", {"buy-green-after-red": 0.9})
    arena = Arena(candles=FAKE_CANDLES, initial_capital=100.0)
    results = arena.evaluate([a1, a2])
    assert len(results) == 2
    assert "a1" in results
    assert "a2" in results
    assert isinstance(a1.fitness, float)
    assert isinstance(a2.fitness, float)

def test_arena_tracks_trades():
    a1 = Agent("a1", {"buy-green-after-red": 1.0, "sell-red-after-green": 1.0})
    arena = Arena(candles=FAKE_CANDLES, initial_capital=100.0)
    arena.evaluate([a1])
    assert isinstance(a1.history, list)
