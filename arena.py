"""Arena V5 — Grid trading simulation.

Agents don't buy/sell directly. They decide WHEN to activate a grid.
The grid itself handles the buy/sell mechanics (like Martin).

Skills control:
- WHEN to start a grid (entry signal)
- WHEN to stop a grid (exit signal / stop loss)
- Grid parameters derived from agent config (spacing, levels, leverage)
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
        grid = None
        # grid = {
        #   "center": price when grid started,
        #   "capital": capital allocated,
        #   "spacing_pct": % between levels,
        #   "levels": int,
        #   "buys": [{"price": float, "filled": bool, "size": float}],
        #   "sells": [{"price": float, "filled": bool, "size": float}],
        #   "round_trips": int,
        #   "profit": float,
        #   "candles_active": int,
        #   "max_loss_pct": float,
        # }
        agent.history = []

        spacing = agent.get_grid_spacing()
        levels = agent.get_grid_levels()
        max_loss = agent.get_stop_loss()
        min_hold = agent.get_min_hold()

        for i in range(1, len(self.candles)):
            candle = self.candles[i]
            prev = self.candles[i - 1]
            price = candle["close"]

            if grid is None:
                # ─── No grid: check if agent wants to start one ───
                action = agent.decide(candle, prev, None)
                if action == "buy" and capital > 1:
                    grid = self._create_grid(price, capital, spacing, levels)
                    capital = 0
                    agent.history.append("grid_start")
            else:
                grid["candles_active"] += 1

                # ─── Simulate grid fills ───
                # Check buys (price went low enough)
                for buy in grid["buys"]:
                    if not buy["filled"] and candle["low"] <= buy["price"]:
                        buy["filled"] = True
                        fee = buy["size"] * buy["price"] * self.fee_rate
                        grid["profit"] -= fee

                # Check sells (price went high enough)
                for j, sell in enumerate(grid["sells"]):
                    if not sell["filled"] and candle["high"] >= sell["price"]:
                        # Only fill if corresponding buy was filled
                        if j < len(grid["buys"]) and grid["buys"][j]["filled"]:
                            sell["filled"] = True
                            # Round trip profit = sell_price - buy_price - fees
                            buy_price = grid["buys"][j]["price"]
                            rt_profit = (sell["price"] - buy_price) * sell["size"]
                            fee = sell["size"] * sell["price"] * self.fee_rate
                            grid["profit"] += rt_profit - fee
                            grid["round_trips"] += 1
                            # Reset buy/sell for reuse
                            grid["buys"][j]["filled"] = False
                            sell["filled"] = False

                # ─── Check grid P&L (unrealized + realized) ───
                unrealized = 0
                for buy in grid["buys"]:
                    if buy["filled"]:
                        unrealized += (price - buy["price"]) * buy["size"]

                total_pnl = grid["profit"] + unrealized
                total_pnl_pct = total_pnl / grid["capital"]

                # ─── Stop loss: kill grid if losing too much ───
                if total_pnl_pct < -max_loss:
                    # Close all open positions at market
                    close_value = grid["capital"] + total_pnl
                    fee = abs(close_value) * self.fee_rate
                    capital = max(0, close_value - fee)
                    agent.history.append(f"grid_stop_rt{grid['round_trips']}")
                    grid = None
                    continue

                # ─── Agent signal to close grid ───
                if grid["candles_active"] >= min_hold:
                    action = agent.decide(candle, prev, {"entry": grid["center"], "peak": grid["center"]})
                    if action == "sell":
                        close_value = grid["capital"] + total_pnl
                        fee = abs(close_value) * self.fee_rate
                        capital = max(0, close_value - fee)
                        agent.history.append(f"grid_close_rt{grid['round_trips']}")
                        grid = None
                        continue

                agent.history.append("grid_active")

        # Close any active grid at end
        if grid is not None:
            unrealized = 0
            last_price = self.candles[-1]["close"]
            for buy in grid["buys"]:
                if buy["filled"]:
                    unrealized += (last_price - buy["price"]) * buy["size"]
            total_pnl = grid["profit"] + unrealized
            capital = max(0, grid["capital"] + total_pnl)

        return round(capital - self.initial_capital, 4)

    def _create_grid(self, center_price, capital, spacing_pct, levels):
        """Create a grid centered on current price."""
        size_per_level = capital / (levels * center_price)

        buys = []
        sells = []
        for n in range(1, levels + 1):
            buy_price = center_price * (1 - spacing_pct * n)
            sell_price = center_price * (1 + spacing_pct * n)
            buys.append({"price": buy_price, "filled": False, "size": size_per_level})
            sells.append({"price": sell_price, "filled": False, "size": size_per_level})

        return {
            "center": center_price,
            "capital": capital,
            "spacing_pct": spacing_pct,
            "levels": levels,
            "buys": buys,
            "sells": sells,
            "round_trips": 0,
            "profit": 0.0,
            "candles_active": 0,
            "max_loss_pct": 0.15,
        }
