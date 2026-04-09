"""Arena — 4 trading modes: Grid, Scalp, Martingale, DCA.

Each mode simulates a different trading strategy.
Agents use their skills to decide WHEN to enter/exit.
The arena handles the mechanics (fills, fees, trailing, stops).
"""
from indicators import enrich_candles


class Arena:
    def __init__(self, candles: list[dict], initial_capital: float = 100.0, config: dict = None):
        self.candles = candles
        self.initial_capital = initial_capital
        self.config = config or {}
        self.fee_rate = self.config.get("fee_pct", 0.05) / 100
        enrich_candles(self.candles)

    def evaluate(self, agents: list) -> dict[str, float]:
        results = {}
        mode = self.config.get("mode", "grid")
        for agent in agents:
            if mode == "grid":
                pnl = self._run_grid(agent)
            elif mode == "scalp":
                pnl = self._run_scalp(agent)
            elif mode == "martingale":
                pnl = self._run_martingale(agent)
            elif mode == "dca":
                pnl = self._run_dca(agent)
            else:
                pnl = self._run_grid(agent)
            agent.fitness = pnl
            results[agent.agent_id] = pnl
        return results

    # ═══════════════════════════════════════════════════════════════════
    # MODE 1: GRID
    # ═══════════════════════════════════════════════════════════════════

    def _run_grid(self, agent) -> float:
        capital = self.initial_capital
        grid = None
        agent.history = []

        spacing = self.config.get("grid_spacing", agent.get_grid_spacing())
        levels = self.config.get("grid_levels", agent.get_grid_levels())
        max_loss = self.config.get("max_loss", agent.get_stop_loss())
        min_hold = self.config.get("min_hold", agent.get_min_hold())

        for i in range(1, len(self.candles)):
            c, prev, price = self.candles[i], self.candles[i-1], self.candles[i]["close"]

            if grid is None:
                if agent.decide(c, prev, None) == "buy" and capital > 1:
                    grid = self._create_grid(price, capital, spacing, levels)
                    capital = 0
                    agent.history.append("grid_start")
            else:
                grid["candles_active"] += 1

                for buy in grid["buys"]:
                    if not buy["filled"] and c["low"] <= buy["price"]:
                        buy["filled"] = True
                        grid["profit"] -= buy["size"] * buy["price"] * self.fee_rate

                for j, sell in enumerate(grid["sells"]):
                    if not sell["filled"] and c["high"] >= sell["price"]:
                        if j < len(grid["buys"]) and grid["buys"][j]["filled"]:
                            sell["filled"] = True
                            rt = (sell["price"] - grid["buys"][j]["price"]) * sell["size"]
                            grid["profit"] += rt - sell["size"] * sell["price"] * self.fee_rate
                            grid["round_trips"] += 1
                            grid["buys"][j]["filled"] = False
                            sell["filled"] = False

                unrealized = sum((price - b["price"]) * b["size"] for b in grid["buys"] if b["filled"])
                total_pnl = grid["profit"] + unrealized

                if total_pnl / grid["capital"] < -max_loss:
                    capital = max(0, grid["capital"] + total_pnl - abs(grid["capital"] + total_pnl) * self.fee_rate)
                    agent.history.append(f"grid_stop_rt{grid['round_trips']}")
                    grid = None
                    continue

                if grid["candles_active"] >= min_hold:
                    if agent.decide(c, prev, {"entry": grid["center"], "peak": grid["center"]}) == "sell":
                        capital = max(0, grid["capital"] + total_pnl - abs(grid["capital"] + total_pnl) * self.fee_rate)
                        agent.history.append(f"grid_close_rt{grid['round_trips']}")
                        grid = None
                        continue

                agent.history.append("grid_active")

        if grid is not None:
            unrealized = sum((self.candles[-1]["close"] - b["price"]) * b["size"] for b in grid["buys"] if b["filled"])
            capital = max(0, grid["capital"] + grid["profit"] + unrealized)

        return round(capital - self.initial_capital, 4)

    def _create_grid(self, center, capital, spacing, levels):
        size = capital / (levels * center)
        return {
            "center": center, "capital": capital, "profit": 0.0, "round_trips": 0, "candles_active": 0,
            "buys": [{"price": center * (1 - spacing * n), "filled": False, "size": size} for n in range(1, levels + 1)],
            "sells": [{"price": center * (1 + spacing * n), "filled": False, "size": size} for n in range(1, levels + 1)],
        }

    # ═══════════════════════════════════════════════════════════════════
    # MODE 2: SCALP — direct buy/sell with trailing
    # ═══════════════════════════════════════════════════════════════════

    def _run_scalp(self, agent) -> float:
        capital = self.initial_capital
        position = None  # {"side","entry","size","peak","trail_level","candles_held"}
        agent.history = []

        trail_pct = self.config.get("trail_pct", agent.get_trail_pct())
        min_profit = self.config.get("mart_min_profit", agent.get_min_profit())
        min_hold = self.config.get("min_hold", agent.get_min_hold())
        stop_loss = self.config.get("sl_pct", agent.get_stop_loss())

        for i in range(1, len(self.candles)):
            c, prev, price = self.candles[i], self.candles[i-1], self.candles[i]["close"]

            if position is None:
                action = agent.decide(c, prev, None)
                if action == "buy" and capital > 1:
                    fee = capital * self.fee_rate
                    size = (capital - fee) / price
                    position = {"side": "long", "entry": price, "size": size, "peak": price, "trail_level": 0, "candles_held": 0}
                    capital = 0
                    agent.history.append("buy")
                elif action in ("sell", "short") and capital > 1:
                    fee = capital * self.fee_rate
                    size = (capital - fee) / price
                    position = {"side": "short", "entry": price, "size": size, "peak": price, "trail_level": 0, "candles_held": 0}
                    capital = 0
                    agent.history.append("short")
            else:
                position["candles_held"] += 1
                if position["side"] == "long":
                    gain = (price - position["entry"]) / position["entry"]
                    if price > position["peak"]: position["peak"] = price
                else:
                    gain = (position["entry"] - price) / position["entry"]
                    if price < position["peak"]: position["peak"] = price

                # Hard stop
                if gain < -stop_loss:
                    capital = self._close(position, price)
                    agent.history.append("stop_loss")
                    position = None
                    continue

                # Trailing
                if position["candles_held"] >= min_hold and gain >= min_profit:
                    if position["side"] == "long":
                        tl = position["peak"] * (1 - trail_pct)
                        if tl > position["trail_level"]: position["trail_level"] = tl
                        if price <= position["trail_level"] and position["trail_level"] > 0:
                            capital = self._close(position, price)
                            agent.history.append("trail_exit")
                            position = None
                            continue
                    else:
                        tl = position["peak"] * (1 + trail_pct)
                        if position["trail_level"] == 0 or tl < position["trail_level"]: position["trail_level"] = tl
                        if price >= position["trail_level"] and position["trail_level"] > 0:
                            capital = self._close(position, price)
                            agent.history.append("trail_exit")
                            position = None
                            continue

                # Signal exit
                if position["candles_held"] >= min_hold:
                    action = agent.decide(c, prev, {"entry": position["entry"], "peak": position["peak"]})
                    if (position["side"] == "long" and action in ("sell", "short")) or \
                       (position["side"] == "short" and action == "buy"):
                        capital = self._close(position, price)
                        agent.history.append("signal_exit")
                        position = None
                        continue

                agent.history.append("hold")

        if position:
            capital = self._close(position, self.candles[-1]["close"])

        return round(capital - self.initial_capital, 4)

    # ═══════════════════════════════════════════════════════════════════
    # MODE 3: MARTINGALE — double down + optional flip long↔short
    # ═══════════════════════════════════════════════════════════════════

    def _run_martingale(self, agent) -> float:
        capital = self.initial_capital
        position = None
        # position = {"side","entries":[{price,size,capital}],"total_size","avg_entry",
        #             "peak","trail_level","candles_since_last","level","last_capital"}
        agent.history = []

        max_levels = self.config.get("mart_levels", agent.get_martingale_levels())
        level_spacing = self.config.get("mart_spacing", agent.get_level_spacing())
        trail_pct = self.config.get("mart_trail", agent.get_trail_pct())
        min_profit = self.config.get("mart_min_profit", agent.get_min_profit())
        stop_loss = self.config.get("mart_sl", agent.get_stop_loss())
        min_hold = self.config.get("min_hold", agent.get_min_hold())
        can_flip = self.config.get("mart_flip", "long-short") == "long-short"

        for i in range(1, len(self.candles)):
            c, prev, price = self.candles[i], self.candles[i-1], self.candles[i]["close"]

            if position is None:
                action = agent.decide(c, prev, None)
                side = None
                if action == "buy" and capital > 1:
                    side = "long"
                elif action in ("sell", "short") and capital > 1:
                    side = "short"
                if side:
                    first_pct = 1.0 / (2 ** min(max_levels, 5))
                    first_cap = max(capital * first_pct, capital * 0.05)
                    fee = first_cap * self.fee_rate
                    size = (first_cap - fee) / price
                    position = {
                        "side": side,
                        "entries": [{"price": price, "size": size, "capital": first_cap}],
                        "total_size": size, "avg_entry": price,
                        "peak": price, "trail_level": 0,
                        "candles_since_last": 0, "level": 1, "last_capital": first_cap,
                    }
                    capital -= first_cap
                    agent.history.append(f"{'long' if side == 'long' else 'short'}_L1")
            else:
                position["candles_since_last"] += 1

                if position["side"] == "long":
                    gain = (price - position["avg_entry"]) / position["avg_entry"]
                    if price > position["peak"]: position["peak"] = price
                    drop = (position["avg_entry"] - price) / position["avg_entry"]
                else:
                    gain = (position["avg_entry"] - price) / position["avg_entry"]
                    if price < position["peak"]: position["peak"] = price
                    drop = (price - position["avg_entry"]) / position["avg_entry"]

                # ── Martingale: double down ──
                if (position["level"] < max_levels
                    and position["candles_since_last"] >= min_hold
                    and capital > 1
                    and drop >= level_spacing * position["level"]):
                    next_cap = min(capital, position["last_capital"] * 2)
                    fee = next_cap * self.fee_rate
                    new_size = (next_cap - fee) / price
                    position["entries"].append({"price": price, "size": new_size, "capital": next_cap})
                    position["total_size"] += new_size
                    total_cost = sum(e["price"] * e["size"] for e in position["entries"])
                    position["avg_entry"] = total_cost / position["total_size"]
                    position["last_capital"] = next_cap
                    position["level"] += 1
                    position["candles_since_last"] = 0
                    position["peak"] = price
                    position["trail_level"] = 0
                    capital -= next_cap
                    agent.history.append(f"double_L{position['level']}")
                    continue

                # ── Hard stop ──
                if gain < -stop_loss:
                    capital += self._close_mart(position, price)
                    agent.history.append(f"stop_L{position['level']}")
                    position = None
                    continue

                # ── Trailing ──
                if position["candles_since_last"] >= min_hold and gain >= min_profit:
                    if position["side"] == "long":
                        tl = position["peak"] * (1 - trail_pct)
                        if tl > position["trail_level"]: position["trail_level"] = tl
                        if price <= position["trail_level"] and position["trail_level"] > 0:
                            capital += self._close_mart(position, price)
                            agent.history.append(f"trail_L{position['level']}")
                            position = None
                            continue
                    else:
                        tl = position["peak"] * (1 + trail_pct)
                        if position["trail_level"] == 0 or tl < position["trail_level"]: position["trail_level"] = tl
                        if price >= position["trail_level"] and position["trail_level"] > 0:
                            capital += self._close_mart(position, price)
                            agent.history.append(f"trail_L{position['level']}")
                            position = None
                            continue

                # ── Signal exit or FLIP ──
                if position["candles_since_last"] >= min_hold:
                    action = agent.decide(c, prev, {"entry": position["avg_entry"], "peak": position["peak"]})
                    should_flip = can_flip and (
                        (position["side"] == "long" and action in ("sell", "short")) or
                        (position["side"] == "short" and action == "buy")
                    )
                    should_exit = (
                        (position["side"] == "long" and action in ("sell", "short")) or
                        (position["side"] == "short" and action == "buy")
                    )
                    if should_flip:
                        # Close current position
                        capital += self._close_mart(position, price)
                        old_side = position["side"]
                        position = None
                        # Immediately open opposite
                        new_side = "short" if old_side == "long" else "long"
                        first_pct = 1.0 / (2 ** min(max_levels, 5))
                        first_cap = max(capital * first_pct, capital * 0.05)
                        fee = first_cap * self.fee_rate
                        size = (first_cap - fee) / price
                        position = {
                            "side": new_side,
                            "entries": [{"price": price, "size": size, "capital": first_cap}],
                            "total_size": size, "avg_entry": price,
                            "peak": price, "trail_level": 0,
                            "candles_since_last": 0, "level": 1, "last_capital": first_cap,
                        }
                        capital -= first_cap
                        agent.history.append(f"flip_{new_side}_L1")
                        continue
                    elif should_exit:
                        capital += self._close_mart(position, price)
                        agent.history.append(f"signal_L{position['level']}")
                        position = None
                        continue

                agent.history.append("hold")

        if position:
            capital += self._close_mart(position, self.candles[-1]["close"])

        return round(capital - self.initial_capital, 4)

    def _close_mart(self, pos, price) -> float:
        """Close a martingale position, return capital recovered."""
        if pos["side"] == "long":
            gross = pos["total_size"] * price
        else:
            pnl = (pos["avg_entry"] - price) * pos["total_size"]
            invested = sum(e["capital"] for e in pos["entries"])
            gross = invested + pnl
        fee = abs(gross) * self.fee_rate
        return max(0, gross - fee)

    # ═══════════════════════════════════════════════════════════════════
    # MODE 4: DCA — fixed interval buys, trailing to exit
    # ═══════════════════════════════════════════════════════════════════

    def _run_dca(self, agent) -> float:
        capital = self.initial_capital
        position = None
        # position = {"entries":[], "total_size", "avg_entry", "peak", "trail_level",
        #             "level", "candles_since_last", "total_invested"}
        agent.history = []

        max_levels = self.config.get("dca_levels", 3)
        level_spacing = self.config.get("dca_spacing", 0.01)
        multiplier = self.config.get("dca_mult", 2.0)
        trail_pct = self.config.get("dca_trail", agent.get_trail_pct())
        min_profit = agent.get_min_profit()
        stop_loss = agent.get_stop_loss()
        min_hold = agent.get_min_hold()

        for i in range(1, len(self.candles)):
            c, prev, price = self.candles[i], self.candles[i-1], self.candles[i]["close"]

            if position is None:
                action = agent.decide(c, prev, None)
                if action == "buy" and capital > 1:
                    # DCA: first buy is 1 / sum_of_multipliers of capital
                    weights = [multiplier ** k for k in range(max_levels)]
                    first_cap = capital / sum(weights)
                    fee = first_cap * self.fee_rate
                    size = (first_cap - fee) / price
                    position = {
                        "entries": [{"price": price, "size": size, "capital": first_cap}],
                        "total_size": size, "avg_entry": price,
                        "peak": price, "trail_level": 0,
                        "level": 1, "candles_since_last": 0,
                        "total_invested": first_cap, "weights": weights,
                    }
                    capital -= first_cap
                    agent.history.append("dca_L1")
            else:
                position["candles_since_last"] += 1

                if price > position["peak"]: position["peak"] = price
                gain = (price - position["avg_entry"]) / position["avg_entry"]

                # ── DCA: add on dip at regular intervals ──
                if (position["level"] < max_levels
                    and position["candles_since_last"] >= min_hold
                    and capital > 1):
                    drop = (position["avg_entry"] - price) / position["avg_entry"]
                    if drop >= level_spacing * position["level"]:
                        next_cap = position["entries"][0]["capital"] * position["weights"][position["level"]]
                        next_cap = min(next_cap, capital)
                        fee = next_cap * self.fee_rate
                        size = (next_cap - fee) / price
                        position["entries"].append({"price": price, "size": size, "capital": next_cap})
                        position["total_size"] += size
                        total_cost = sum(e["price"] * e["size"] for e in position["entries"])
                        position["avg_entry"] = total_cost / position["total_size"]
                        position["total_invested"] += next_cap
                        position["level"] += 1
                        position["candles_since_last"] = 0
                        position["peak"] = price
                        position["trail_level"] = 0
                        capital -= next_cap
                        agent.history.append(f"dca_L{position['level']}")
                        continue

                # ── Hard stop ──
                if gain < -stop_loss:
                    gross = position["total_size"] * price
                    fee = gross * self.fee_rate
                    capital += max(0, gross - fee)
                    agent.history.append(f"dca_stop_L{position['level']}")
                    position = None
                    continue

                # ── Trailing ──
                if position["candles_since_last"] >= min_hold and gain >= min_profit:
                    tl = position["peak"] * (1 - trail_pct)
                    if tl > position["trail_level"]: position["trail_level"] = tl
                    if price <= position["trail_level"] and position["trail_level"] > 0:
                        gross = position["total_size"] * price
                        fee = gross * self.fee_rate
                        capital += max(0, gross - fee)
                        agent.history.append(f"dca_trail_L{position['level']}")
                        position = None
                        continue

                # ── Signal exit ──
                if position["candles_since_last"] >= min_hold:
                    action = agent.decide(c, prev, {"entry": position["avg_entry"], "peak": position["peak"]})
                    if action == "sell":
                        gross = position["total_size"] * price
                        fee = gross * self.fee_rate
                        capital += max(0, gross - fee)
                        agent.history.append(f"dca_close_L{position['level']}")
                        position = None
                        continue

                agent.history.append("hold")

        if position:
            gross = position["total_size"] * self.candles[-1]["close"]
            capital += max(0, gross - gross * self.fee_rate)

        return round(capital - self.initial_capital, 4)

    # ═══════════════════════════════════════════════════════════════════
    # SHARED HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def _close(self, pos, price) -> float:
        """Close a simple long/short position."""
        if pos["side"] == "long":
            gross = pos["size"] * price
        else:
            pnl = (pos["entry"] - price) * pos["size"]
            gross = pos["size"] * pos["entry"] + pnl
        fee = abs(gross) * self.fee_rate
        return max(0, gross - fee)
