import pytest
from agent import Agent, create_random_agent, SKILL_POOL

def test_agent_has_skills():
    a = Agent(agent_id="test-001", skills={"sell-when-high-touches-resistance": 0.8, "trailing-stop-2pct": 0.6})
    assert len(a.skills) == 2
    assert a.alive is True
    assert a.fitness == 0.0

def test_agent_decide_returns_action():
    a = Agent(agent_id="test-002", skills={"buy-on-dip-3pct": 0.9})
    candle = {"open": 100, "high": 102, "low": 97, "close": 98, "timestamp": 0, "volume": 1000}
    prev = {"open": 101, "high": 103, "low": 100, "close": 101, "timestamp": 0, "volume": 900}
    action = a.decide(candle, prev)
    assert action in ("buy", "sell", "hold")

def test_create_random_agent():
    a = create_random_agent(num_skills=3)
    assert len(a.skills) == 3
    assert a.agent_id.startswith("agent-")

def test_skill_pool_not_empty():
    assert len(SKILL_POOL) >= 10
