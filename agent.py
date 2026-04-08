"""Trading agent — a weighted set of skills that produce buy/sell/hold decisions."""
import random
import uuid
import os
import re
import yaml
from pathlib import Path

# Skill pool: each skill is a (name, condition_fn) tuple
# condition_fn(candle, prev_candle, position) -> "buy" | "sell" | None
SKILL_POOL = {}

def _pct_change(a, b):
    return (b - a) / a if a != 0 else 0

def _register(name):
    def decorator(fn):
        SKILL_POOL[name] = fn
        return fn
    return decorator

@_register("buy-on-dip-0.5pct")
def _(candle, prev, pos):
    if _pct_change(prev["close"], candle["close"]) < -0.005:
        return "buy"

@_register("buy-on-dip-1pct")
def _(candle, prev, pos):
    if _pct_change(prev["close"], candle["close"]) < -0.01:
        return "buy"

@_register("buy-on-dip-2pct")
def _(candle, prev, pos):
    if _pct_change(prev["close"], candle["close"]) < -0.02:
        return "buy"

@_register("sell-on-pump-0.5pct")
def _(candle, prev, pos):
    if _pct_change(prev["close"], candle["close"]) > 0.005:
        return "sell"

@_register("sell-on-pump-1pct")
def _(candle, prev, pos):
    if _pct_change(prev["close"], candle["close"]) > 0.01:
        return "sell"

@_register("sell-on-pump-2pct")
def _(candle, prev, pos):
    if _pct_change(prev["close"], candle["close"]) > 0.02:
        return "sell"

@_register("buy-when-low-touches-support")
def _(candle, prev, pos):
    if candle["low"] < prev["low"] and candle["close"] > candle["open"]:
        return "buy"

@_register("sell-when-high-touches-resistance")
def _(candle, prev, pos):
    if candle["high"] > prev["high"] and candle["close"] < candle["open"]:
        return "sell"

@_register("buy-green-after-red")
def _(candle, prev, pos):
    if prev["close"] < prev["open"] and candle["close"] > candle["open"]:
        return "buy"

@_register("sell-red-after-green")
def _(candle, prev, pos):
    if prev["close"] > prev["open"] and candle["close"] < candle["open"]:
        return "sell"

@_register("hold-in-low-volume")
def _(candle, prev, pos):
    if candle["volume"] < prev["volume"] * 0.5:
        return "hold"

@_register("trailing-stop-1pct")
def _(candle, prev, pos):
    if pos and pos.get("peak", 0) > 0:
        drawdown = _pct_change(pos["peak"], candle["close"])
        if drawdown < -0.01:
            return "sell"

@_register("trailing-stop-3pct")
def _(candle, prev, pos):
    if pos and pos.get("peak", 0) > 0:
        drawdown = _pct_change(pos["peak"], candle["close"])
        if drawdown < -0.03:
            return "sell"

@_register("never-buy-in-downtrend")
def _(candle, prev, pos):
    if candle["close"] < prev["close"] < prev["open"]:
        return "hold"

@_register("take-profit-2pct")
def _(candle, prev, pos):
    if pos and pos.get("entry", 0) > 0:
        gain = _pct_change(pos["entry"], candle["close"])
        if gain > 0.02:
            return "sell"

@_register("take-profit-5pct")
def _(candle, prev, pos):
    if pos and pos.get("entry", 0) > 0:
        gain = _pct_change(pos["entry"], candle["close"])
        if gain > 0.05:
            return "sell"

@_register("buy-on-big-red-body")
def _(candle, prev, pos):
    body = abs(candle["close"] - candle["open"])
    full_range = candle["high"] - candle["low"]
    if full_range > 0 and body / full_range > 0.7 and candle["close"] < candle["open"]:
        return "buy"

@_register("sell-on-big-green-body")
def _(candle, prev, pos):
    body = abs(candle["close"] - candle["open"])
    full_range = candle["high"] - candle["low"]
    if full_range > 0 and body / full_range > 0.7 and candle["close"] > candle["open"]:
        return "sell"

@_register("buy-when-close-near-low")
def _(candle, prev, pos):
    rng = candle["high"] - candle["low"]
    if rng > 0 and (candle["close"] - candle["low"]) / rng < 0.2:
        return "buy"

@_register("sell-when-close-near-high")
def _(candle, prev, pos):
    rng = candle["high"] - candle["low"]
    if rng > 0 and (candle["high"] - candle["close"]) / rng < 0.2:
        return "sell"

def load_metaclaw_skills():
    """Load skill names from cerveau-nb/skills/ auto-skills."""
    skills_dir = Path(__file__).parent.parent.parent / "cerveau-nb" / "skills"
    names = []
    for f in skills_dir.glob("auto-*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            if content.startswith("---"):
                end = content.index("---", 3)
                body = content[end+3:].strip()
                rule = body.split("\n")[0] if body else f.stem
                names.append(rule[:60])
        except Exception:
            continue
    return names


class Agent:
    def __init__(self, agent_id: str, skills: dict[str, float], generation: int = 0, parent_ids: list[str] = None):
        self.agent_id = agent_id
        self.skills = skills  # {skill_name: weight 0-1}
        self.generation = generation
        self.parent_ids = parent_ids or []
        self.fitness = 0.0
        self.alive = True
        self.history = []  # list of actions taken

    def decide(self, candle: dict, prev_candle: dict, position: dict = None) -> str:
        """Weighted vote across all skills."""
        votes = {"buy": 0.0, "sell": 0.0, "hold": 0.0}
        for skill_name, weight in self.skills.items():
            fn = SKILL_POOL.get(skill_name)
            if fn:
                result = fn(candle, prev_candle, position)
                if result:
                    votes[result] += weight
        if votes["buy"] == votes["sell"] == votes["hold"] == 0:
            return "hold"
        return max(votes, key=votes.get)

    def to_dict(self) -> dict:
        return {
            "id": self.agent_id,
            "generation": self.generation,
            "skills": self.skills,
            "fitness": round(self.fitness, 4),
            "alive": self.alive,
            "parent_ids": self.parent_ids,
        }


def create_random_agent(num_skills: int = 4, generation: int = 0) -> Agent:
    pool = list(SKILL_POOL.keys())
    chosen = random.sample(pool, min(num_skills, len(pool)))
    skills = {s: round(random.uniform(0.3, 1.0), 2) for s in chosen}
    return Agent(
        agent_id=f"agent-{uuid.uuid4().hex[:6]}",
        skills=skills,
        generation=generation,
    )
