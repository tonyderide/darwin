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

# ─── Momentum / Micro-structure skills ───

@_register("buy-hammer")
def _(candle, prev, pos):
    """Hammer candle: long lower wick, small body at top."""
    body = abs(candle["close"] - candle["open"])
    rng = candle["high"] - candle["low"]
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    if rng > 0 and lower_wick / rng > 0.6 and body / rng < 0.3:
        return "buy"

@_register("sell-shooting-star")
def _(candle, prev, pos):
    """Shooting star: long upper wick, small body at bottom."""
    body = abs(candle["close"] - candle["open"])
    rng = candle["high"] - candle["low"]
    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    if rng > 0 and upper_wick / rng > 0.6 and body / rng < 0.3:
        return "sell"

@_register("buy-volume-spike")
def _(candle, prev, pos):
    """Buy when volume > 3x previous and candle is green."""
    if prev["volume"] > 0 and candle["volume"] > prev["volume"] * 3 and candle["close"] > candle["open"]:
        return "buy"

@_register("sell-volume-spike")
def _(candle, prev, pos):
    """Sell when volume > 3x previous and candle is red."""
    if prev["volume"] > 0 and candle["volume"] > prev["volume"] * 3 and candle["close"] < candle["open"]:
        return "sell"

@_register("buy-engulfing")
def _(candle, prev, pos):
    """Bullish engulfing: green candle body covers entire previous red body."""
    if prev["close"] < prev["open"] and candle["close"] > candle["open"]:
        if candle["close"] > prev["open"] and candle["open"] < prev["close"]:
            return "buy"

@_register("sell-engulfing")
def _(candle, prev, pos):
    """Bearish engulfing: red candle body covers entire previous green body."""
    if prev["close"] > prev["open"] and candle["close"] < candle["open"]:
        if candle["close"] < prev["open"] and candle["open"] > prev["close"]:
            return "sell"

@_register("buy-double-green")
def _(candle, prev, pos):
    """Two consecutive green candles with increasing size."""
    if candle["close"] > candle["open"] and prev["close"] > prev["open"]:
        cur_body = candle["close"] - candle["open"]
        prev_body = prev["close"] - prev["open"]
        if cur_body > prev_body:
            return "buy"

@_register("sell-double-red")
def _(candle, prev, pos):
    """Two consecutive red candles with increasing size."""
    if candle["close"] < candle["open"] and prev["close"] < prev["open"]:
        cur_body = candle["open"] - candle["close"]
        prev_body = prev["open"] - prev["close"]
        if cur_body > prev_body:
            return "sell"

@_register("buy-breakout-high")
def _(candle, prev, pos):
    """Price breaks above previous high with conviction (close near candle high)."""
    if candle["close"] > prev["high"]:
        rng = candle["high"] - candle["low"]
        if rng > 0 and (candle["close"] - candle["low"]) / rng > 0.8:
            return "buy"

@_register("sell-breakdown-low")
def _(candle, prev, pos):
    """Price breaks below previous low with conviction."""
    if candle["close"] < prev["low"]:
        rng = candle["high"] - candle["low"]
        if rng > 0 and (candle["high"] - candle["close"]) / rng > 0.8:
            return "sell"

@_register("trailing-stop-0.5pct")
def _(candle, prev, pos):
    """Tight trailing stop for scalping."""
    if pos and pos.get("peak", 0) > 0:
        drawdown = _pct_change(pos["peak"], candle["close"])
        if drawdown < -0.005:
            return "sell"

@_register("take-profit-1pct")
def _(candle, prev, pos):
    if pos and pos.get("entry", 0) > 0:
        gain = _pct_change(pos["entry"], candle["close"])
        if gain > 0.01:
            return "sell"

@_register("hold-if-tiny-range")
def _(candle, prev, pos):
    """Don't trade when candle range is tiny (< 0.1% of price)."""
    rng = candle["high"] - candle["low"]
    if candle["close"] > 0 and rng / candle["close"] < 0.001:
        return "hold"

# ─── Autobot strategies (EMA, RSI, BB, ADX) ───

@_register("buy-ema-golden-cross")
def _(candle, prev, pos):
    """EMA50 crosses above EMA200 — bullish trend."""
    if (candle.get("ema50") and candle.get("ema200") and prev.get("ema50") and prev.get("ema200")):
        if prev["ema50"] <= prev["ema200"] and candle["ema50"] > candle["ema200"]:
            return "buy"

@_register("sell-ema-death-cross")
def _(candle, prev, pos):
    """EMA50 crosses below EMA200 — bearish trend."""
    if (candle.get("ema50") and candle.get("ema200") and prev.get("ema50") and prev.get("ema200")):
        if prev["ema50"] >= prev["ema200"] and candle["ema50"] < candle["ema200"]:
            return "sell"

@_register("buy-ema-trend-up")
def _(candle, prev, pos):
    """EMA50 > EMA200 AND RSI > 50 — confirmed uptrend (autobot EMA_TREND)."""
    if candle.get("ema50") and candle.get("ema200") and candle.get("rsi"):
        if candle["ema50"] > candle["ema200"] and candle["rsi"] > 50:
            return "buy"

@_register("sell-ema-trend-down")
def _(candle, prev, pos):
    """EMA50 < EMA200 AND RSI < 50 — confirmed downtrend."""
    if candle.get("ema50") and candle.get("ema200") and candle.get("rsi"):
        if candle["ema50"] < candle["ema200"] and candle["rsi"] < 50:
            return "sell"

@_register("buy-rsi-oversold")
def _(candle, prev, pos):
    """RSI < 30 — oversold bounce."""
    if candle.get("rsi") and candle["rsi"] < 30:
        return "buy"

@_register("sell-rsi-overbought")
def _(candle, prev, pos):
    """RSI > 70 — overbought, take profit."""
    if candle.get("rsi") and candle["rsi"] > 70:
        return "sell"

@_register("buy-bb-lower")
def _(candle, prev, pos):
    """Price touches lower Bollinger Band — mean reversion buy (autobot BB Scalp)."""
    if candle.get("bb_lower") and candle["close"] <= candle["bb_lower"]:
        return "buy"

@_register("sell-bb-upper")
def _(candle, prev, pos):
    """Price touches upper Bollinger Band — mean reversion sell."""
    if candle.get("bb_upper") and candle["close"] >= candle["bb_upper"]:
        return "sell"

@_register("buy-bb-squeeze")
def _(candle, prev, pos):
    """BB Squeeze + price above mid band — compression breakout up (autobot BB_SQUEEZE)."""
    if candle.get("bb_squeeze") and candle.get("bb_mid"):
        if candle["close"] > candle["bb_mid"]:
            return "buy"

@_register("sell-bb-squeeze-down")
def _(candle, prev, pos):
    """BB Squeeze + price below mid band — compression breakout down."""
    if candle.get("bb_squeeze") and candle.get("bb_mid"):
        if candle["close"] < candle["bb_mid"]:
            return "sell"

@_register("buy-composite")
def _(candle, prev, pos):
    """Autobot COMPOSITE: BB_SQUEEZE + EMA_TREND + RSI > 50 simultaneously."""
    if (candle.get("bb_squeeze") and candle.get("ema50") and candle.get("ema200") and candle.get("rsi")):
        if candle["ema50"] > candle["ema200"] and candle["rsi"] > 50:
            return "buy"

@_register("hold-adx-too-strong")
def _(candle, prev, pos):
    """Don't enter if ADX > 40 — trend too strong, risky for grid (autobot ADX filter)."""
    if candle.get("adx") and candle["adx"] > 40:
        return "hold"

@_register("hold-adx-no-trend")
def _(candle, prev, pos):
    """Don't enter if ADX < 15 — no trend, choppy market."""
    if candle.get("adx") and candle["adx"] < 15:
        return "hold"

@_register("buy-ema8-cross-ema21")
def _(candle, prev, pos):
    """Fast EMA8 crosses above EMA21 — short-term momentum."""
    if (candle.get("ema8") and candle.get("ema21") and prev.get("ema8") and prev.get("ema21")):
        if prev["ema8"] <= prev["ema21"] and candle["ema8"] > candle["ema21"]:
            return "buy"

@_register("sell-ema8-cross-ema21")
def _(candle, prev, pos):
    """Fast EMA8 crosses below EMA21 — short-term reversal."""
    if (candle.get("ema8") and candle.get("ema21") and prev.get("ema8") and prev.get("ema21")):
        if prev["ema8"] >= prev["ema21"] and candle["ema8"] < candle["ema21"]:
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
        """Weighted vote across all skills. Returns buy/sell/short/hold."""
        votes = {"buy": 0.0, "sell": 0.0, "hold": 0.0}
        for skill_name, weight in self.skills.items():
            fn = SKILL_POOL.get(skill_name)
            if fn:
                result = fn(candle, prev_candle, position)
                if result:
                    votes[result] += weight
        if votes["buy"] == votes["sell"] == votes["hold"] == 0:
            return "hold"
        action = max(votes, key=votes.get)
        # If no position and sell signal → short
        if action == "sell" and (position is None or position == {}):
            return "short"
        return action

    def get_trail_pct(self) -> float:
        """Trailing stop distance (% below peak). Derived from trailing skills."""
        if "trailing-stop-0.5pct" in self.skills:
            return 0.005 * self.skills["trailing-stop-0.5pct"]
        if "trailing-stop-1pct" in self.skills:
            return 0.01 * self.skills["trailing-stop-1pct"]
        if "trailing-stop-3pct" in self.skills:
            return 0.03 * self.skills["trailing-stop-3pct"]
        return 0.02  # default 2%

    def get_min_profit(self) -> float:
        """Minimum gain% before trailing activates. Derived from take-profit skills."""
        if "take-profit-1pct" in self.skills:
            return 0.005  # activate trail at 0.5% (half of TP target)
        if "take-profit-2pct" in self.skills:
            return 0.01
        if "take-profit-5pct" in self.skills:
            return 0.02
        return 0.003  # default: activate at 0.3% gain

    def get_min_hold(self) -> int:
        """Minimum candles before any exit allowed."""
        if "hold-if-tiny-range" in self.skills:
            return 5
        return 3  # default: hold at least 3 candles

    def get_stop_loss(self) -> float:
        """Hard stop loss percentage."""
        if "trailing-stop-0.5pct" in self.skills:
            return 0.015
        if "trailing-stop-1pct" in self.skills:
            return 0.03
        return 0.05  # default 5% hard stop

    def get_martingale_levels(self) -> int:
        """How many times to double down. More buy signals = more levels."""
        buy_skills = sum(1 for s in self.skills if s.startswith("buy-"))
        if buy_skills >= 4:
            return 4
        if buy_skills >= 2:
            return 3
        return 2  # minimum 2 levels

    def get_level_spacing(self) -> float:
        """Required % drop between martingale levels."""
        if "buy-on-dip-0.5pct" in self.skills:
            return 0.005
        if "buy-on-dip-1pct" in self.skills:
            return 0.01
        if "buy-on-dip-2pct" in self.skills:
            return 0.02
        return 0.01  # default 1% between levels

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
