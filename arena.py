"""Arena V4 — Martingale + trailing + long/short + fees.

Each agent can buy multiple times (martingale levels).
Each buy doubles the size. Average entry drops.
Trail activates once in profit. Hard stop on total position.
"""
from indicators import enrich_candles


class Arena:
    def __init__(self, candles: list[dict], initial_capital: float = 100.0, fee_pct: float = 0.05):
        self.candles = candles
        self.initial_capital = initial_capital
        self.fee_rate = fee_pct / 100
        enrich_candles(self.candles)

    def evaluate(self, agents: list) -> dict[str, float]:
        results = {}
        for agent in agents:
            pnl = self._run_agent(agent)
            agent.fitness = pnl
            results[agent.agent_id] = pnl
        return results

    def _run_agent(self, agent) -> float:
        capital = self.initial_capital
        position = None
        # position = {
        #   "entries": [{"price": float, "size": float, "capital_used": float}],
        #   "total_size": float,
        #   "avg_entry": float,
        #   "peak": float (best price since last entry),
        #   "trail_level": float,
        #   "candles_since_last_entry": int,
        #   "level": int (how many martingale entries),
        # }
        agent.history = []

        trail_pct = agent.get_trail_pct()
        min_profit = agent.get_min_profit()
        min_hold = agent.get_min_hold()
        stop_loss_pct = agent.get_stop_loss()
        max_levels = agent.get_martingale_levels()
        level_spacing = agent.get_level_spacing()

        for i in range(1, len(self.candles)):
            candle = self.candles[i]
            prev = self.candles[i - 1]
            price = candle["close"]

            if position is None:
                # ─── No position: check entry ───
                action = agent.decide(candle, prev, None)
                if action == "buy" and capital > 1:
                    position = self._open_martingale(capital, price, max_levels)
                    capital = position["remaining_capital"]
                    agent.history.append("entry_L1")

            else:
                position["candles_since_last_entry"] += 1

                # Update peak
                if price > position["peak"]:
                    position["peak"] = price

                gain_pct = (price - position["avg_entry"]) / position["avg_entry"]
                total_value = position["total_size"] * price

                # ─── Martingale: add on dip ───
                if (position["level"] < max_levels
                    and position["candles_since_last_entry"] >= min_hold
                    and capital > 1):
                    # Check if price dropped enough from avg entry for next level
                    drop_from_avg = (position["avg_entry"] - price) / position["avg_entry"]
                    required_drop = level_spacing * position["level"]

                    if drop_from_avg >= required_drop:
                        # Double down
                        level_capital = min(capital, position["capital_used_last"] * 2)
                        fee = level_capital * self.fee_rate
                        size = (level_capital - fee) / price
                        position["entries"].append({"price": price, "size": size, "capital_used": level_capital})
                        position["total_size"] += size
                        total_cost = sum(e["price"] * e["size"] for e in position["entries"])
                        position["avg_entry"] = total_cost / position["total_size"]
                        position["capital_used_last"] = level_capital
                        position["level"] += 1
                        position["candles_since_last_entry"] = 0
                        position["peak"] = price
                        position["trail_level"] = 0
                        capital -= level_capital
                        agent.history.append(f"entry_L{position['level']}")
                        continue

                # ─── Hard stop loss on total position ───
                if gain_pct < -stop_loss_pct:
                    gross = position["total_size"] * price
                    fee = gross * self.fee_rate
                    capital += max(0, gross - fee)
                    agent.history.append(f"stop_L{position['level']}")
                    position = None
                    continue

                # ─── Trailing exit ───
                if (position["candles_since_last_entry"] >= min_hold
                    and gain_pct >= min_profit):
                    trail_level = position["peak"] * (1 - trail_pct)
                    if trail_level > position["trail_level"]:
                        position["trail_level"] = trail_level
                    if price <= position["trail_level"] and position["trail_level"] > 0:
                        gross = position["total_size"] * price
                        fee = gross * self.fee_rate
                        capital += max(0, gross - fee)
                        agent.history.append(f"trail_L{position['level']}")
                        position = None
                        continue

                # ─── Signal exit (respect min_hold) ───
                if position["candles_since_last_entry"] >= min_hold:
                    action = agent.decide(candle, prev, {"entry": position["avg_entry"], "peak": position["peak"]})
                    if action == "sell":
                        gross = position["total_size"] * price
                        fee = gross * self.fee_rate
                        capital += max(0, gross - fee)
                        agent.history.append(f"signal_L{position['level']}")
                        position = None
                        continue

                agent.history.append("hold")

        # Close open position at end
        if position is not None:
            gross = position["total_size"] * self.candles[-1]["close"]
            fee = gross * self.fee_rate
            capital += max(0, gross - fee)

        return round(capital - self.initial_capital, 4)

    def _open_martingale(self, capital, price, max_levels):
        """Open first martingale level. Uses 1/(2^max_levels) of capital."""
        # First level: small portion (so we have room to double down)
        first_pct = 1.0 / (2 ** min(max_levels, 5))  # e.g. 3 levels → 12.5%
        first_capital = capital * max(first_pct, 0.05)  # minimum 5%

        fee = first_capital * self.fee_rate
        size = (first_capital - fee) / price

        return {
            "entries": [{"price": price, "size": size, "capital_used": first_capital}],
            "total_size": size,
            "avg_entry": price,
            "peak": price,
            "trail_level": 0,
            "candles_since_last_entry": 0,
            "level": 1,
            "capital_used_last": first_capital,
            "remaining_capital": capital - first_capital,
        }
