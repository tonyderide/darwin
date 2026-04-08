"""Arena — evaluate agents on historical OHLC candles."""


class Arena:
    def __init__(self, candles: list[dict], initial_capital: float = 100.0, fee_pct: float = 0.05):
        self.candles = candles
        self.initial_capital = initial_capital
        self.fee_rate = fee_pct / 100  # 0.05% = 0.0005

    def evaluate(self, agents: list) -> dict[str, float]:
        """Run all agents through the candle series. Return {agent_id: pnl}."""
        results = {}
        for agent in agents:
            pnl = self._run_agent(agent)
            agent.fitness = pnl
            results[agent.agent_id] = pnl
        return results

    def _run_agent(self, agent) -> float:
        capital = self.initial_capital
        position = None  # {"entry": price, "size": units, "peak": highest_since_entry}
        agent.history = []

        for i in range(1, len(self.candles)):
            candle = self.candles[i]
            prev = self.candles[i - 1]
            action = agent.decide(candle, prev, position)
            agent.history.append(action)

            if action == "buy" and position is None:
                fee = capital * self.fee_rate
                capital_after_fee = capital - fee
                size = capital_after_fee / candle["close"]
                position = {"entry": candle["close"], "size": size, "peak": candle["close"]}
                capital = 0

            elif action == "sell" and position is not None:
                gross = position["size"] * candle["close"]
                fee = gross * self.fee_rate
                capital = gross - fee
                position = None

            elif position is not None:
                if candle["close"] > position["peak"]:
                    position["peak"] = candle["close"]

        # Close any open position at end
        if position is not None:
            capital = position["size"] * self.candles[-1]["close"]

        return round(capital - self.initial_capital, 4)
