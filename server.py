"""WebSocket server — runs evolution and pushes events to frontend."""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import websockets

from data import fetch_ohlc
from tick_fetcher import load_ticks, ticks_to_candles, DATA_DIR
from agent import Agent, create_random_agent, SKILL_POOL
from arena import Arena
from evolution import select_survivors, evolve_generation


# Kraken symbol → tick pair mapping
TICK_PAIRS = {
    "PF_SOLUSD": "SOLUSD", "PF_DOTUSD": "DOTUSD", "PF_ADAUSD": "ADAUSD",
    "PF_ETHUSD": "ETHUSD", "PF_XBTUSD": "XXBTZUSD", "PF_AVAXUSD": "AVAXUSD",
    "PF_LINKUSD": "LINKUSD",
}


def get_candles(symbol: str, interval: int, date_from: str = None, date_to: str = None) -> tuple[list[dict], str]:
    """Try tick cache first, fallback to Kraken API. Returns (candles, source)."""
    tick_pair = TICK_PAIRS.get(symbol, symbol.replace("PF_", ""))
    tick_file = DATA_DIR / f"{tick_pair}.jsonl"

    if tick_file.exists():
        ticks = load_ticks(tick_pair, date_from, date_to)
        if len(ticks) > 100:
            candles = ticks_to_candles(ticks, interval_seconds=interval * 60)
            return candles, f"ticks ({len(ticks):,} ticks → {len(candles):,} candles)"

    # Fallback to Kraken OHLC API
    candles = fetch_ohlc(symbol, interval=interval, count=8760)
    if date_from or date_to:
        candles = filter_candles_by_date(candles, date_from, date_to)
    return candles, f"API ({len(candles):,} candles)"


def filter_candles_by_date(candles, date_from=None, date_to=None):
    """Filter candles by date range (ISO strings like '2025-06-01')."""
    if not date_from and not date_to:
        return candles
    filtered = candles
    if date_from:
        ts_from = int(datetime.fromisoformat(date_from).timestamp() * 1000)
        filtered = [c for c in filtered if c["timestamp"] >= ts_from]
    if date_to:
        ts_to = int(datetime.fromisoformat(date_to + "T23:59:59").timestamp() * 1000)
        filtered = [c for c in filtered if c["timestamp"] <= ts_to]
    return filtered

PORT = int(os.environ.get("DARWIN_PORT", 8765))
clients = set()

async def broadcast(event: dict):
    msg = json.dumps(event)
    for ws in list(clients):
        try:
            await ws.send(msg)
        except websockets.ConnectionClosed:
            clients.discard(ws)

async def run_evolution(config: dict):
    """Main evolution loop."""
    pop_size = config.get("population", 8)
    generations = config.get("generations", 10)
    mutation_rate = config.get("mutation_rate", 0.3)
    kill_ratio = config.get("kill_ratio", 0.3)
    symbol = config.get("symbol", "PF_SOLUSD")
    interval = config.get("interval", 240)

    date_from = config.get("date_from")
    date_to = config.get("date_to")

    await broadcast({"type": "status", "message": f"Loading {symbol} data ({interval}min)..."})
    all_candles, source = get_candles(symbol, interval, date_from, date_to)

    if not all_candles or len(all_candles) < 100:
        await broadcast({"type": "error", "message": f"Not enough data: {len(all_candles) if all_candles else 0} candles (need 100+). Try fetching ticks first: python tick_fetcher.py --pair {TICK_PAIRS.get(symbol, symbol)} --days 30"})
        return

    await broadcast({"type": "status", "message": f"Source: {source}. Splitting into quarters..."})

    # Split into 4 quarters (Q1=oldest, Q4=most recent)
    q_size = len(all_candles) // 4
    quarters = [
        all_candles[i * q_size : (i + 1) * q_size]
        for i in range(4)
    ]
    # Label each quarter with actual date range
    def _date_label(candles_slice):
        d0 = datetime.fromtimestamp(candles_slice[0]["timestamp"] / 1000).strftime("%m/%d")
        d1 = datetime.fromtimestamp(candles_slice[-1]["timestamp"] / 1000).strftime("%m/%d")
        return f"{d0}-{d1}"

    quarter_labels = [f"Q{i+1} ({_date_label(quarters[i])})" for i in range(4)]

    # Train on Q1 (oldest period)
    train_candles = quarters[0]

    await broadcast({"type": "status", "message": f"Source: {source}. {len(all_candles)} candles, 4 periods of ~{q_size}. Training on {quarter_labels[0]}..."})

    agents = [create_random_agent(num_skills=4, generation=0) for _ in range(pop_size)]
    arena = Arena(candles=train_candles, config=config)

    await broadcast({
        "type": "init",
        "agents": [a.to_dict() for a in agents],
        "skill_pool": list(SKILL_POOL.keys()),
        "candle_count": len(train_candles),
        "symbol": symbol,
        "phase": "train",
    })

    for gen in range(generations):
        arena.evaluate(agents)

        await broadcast({
            "type": "generation",
            "gen": gen,
            "agents": [a.to_dict() for a in agents],
        })

        await asyncio.sleep(0.5)

        survivors = select_survivors(agents, kill_ratio)
        dead = [a for a in agents if not a.alive]

        if dead:
            await broadcast({
                "type": "deaths",
                "gen": gen,
                "dead": [a.to_dict() for a in dead],
            })
            await asyncio.sleep(0.8)

        agents = evolve_generation(agents, target_size=pop_size, kill_ratio=0, mutation_rate=mutation_rate)

        new_agents = [a for a in agents if a.agent_id not in {s.agent_id for s in survivors}]
        if new_agents:
            await broadcast({
                "type": "births",
                "gen": gen,
                "born": [a.to_dict() for a in new_agents],
            })
            await asyncio.sleep(0.5)

    # ── Phase 1: Final training evaluation ──
    for a in agents:
        a.alive = True
    arena.evaluate(agents)
    ranked = sorted(agents, key=lambda a: a.fitness, reverse=True)
    train_results = {a.agent_id: a.fitness for a in ranked}

    await broadcast({
        "type": "complete",
        "phase": "train",
        "final_ranking": [a.to_dict() for a in ranked],
        "best": ranked[0].to_dict() if ranked else None,
    })

    await asyncio.sleep(3)

    # ── Phase 2: Test survivors on Q2, Q3, Q4 ──
    quarter_results = []
    quarter_results.append({
        "label": quarter_labels[0],
        "candles": len(quarters[0]),
        "results": [{**a.to_dict(), "pnl": round(train_results.get(a.agent_id, 0), 4)} for a in ranked[:10]],
    })

    for qi in range(1, 4):
        await broadcast({"type": "status", "message": f"Testing on {quarter_labels[qi]} ({len(quarters[qi])} candles)..."})
        await asyncio.sleep(0.5)

        test_arena = Arena(candles=quarters[qi], config=config)
        test_arena.evaluate(agents)
        test_ranked = sorted(agents, key=lambda a: a.fitness, reverse=True)

        quarter_results.append({
            "label": quarter_labels[qi],
            "candles": len(quarters[qi]),
            "results": [{**a.to_dict(), "pnl": round(a.fitness, 4)} for a in test_ranked[:10]],
        })

        await broadcast({
            "type": "quarter_result",
            "quarter": qi,
            "label": quarter_labels[qi],
            "ranking": [a.to_dict() for a in test_ranked[:5]],
            "best": test_ranked[0].to_dict() if test_ranked else None,
        })
        await asyncio.sleep(1)

    # ── Final: comparison across all quarters ──
    # For each agent, collect PnL per quarter
    agent_across = {}
    for qi, qr in enumerate(quarter_results):
        for entry in qr["results"]:
            aid = entry["id"]
            if aid not in agent_across:
                agent_across[aid] = {"id": aid, "skills": entry["skills"], "quarters": {}}
            agent_across[aid]["quarters"][quarter_labels[qi]] = entry["pnl"]

    # Score = number of quarters with positive PnL (robustness)
    for aid, data in agent_across.items():
        data["positive_quarters"] = sum(1 for v in data["quarters"].values() if v > 0)
        data["total_pnl"] = round(sum(data["quarters"].values()), 4)

    robust_ranked = sorted(agent_across.values(), key=lambda x: (x["positive_quarters"], x["total_pnl"]), reverse=True)

    await broadcast({
        "type": "robustness_report",
        "quarters": quarter_labels,
        "agents": robust_ranked[:10],
        "most_robust": robust_ranked[0] if robust_ranked else None,
    })

async def replay_agent(config: dict):
    """Replay a single agent as a GRID bot, return equity + trade log."""
    agent_data = config.get("agent", {})
    symbol = config.get("symbol", "PF_SOLUSD")
    date_from = config.get("date_from")
    date_to = config.get("date_to")
    interval = config.get("interval", 240)

    await broadcast({"type": "status", "message": f"Grid replay {agent_data.get('id', '?')} on {symbol}..."})

    candles, source = get_candles(symbol, interval, date_from, date_to)
    if not candles or len(candles) < 10:
        await broadcast({"type": "error", "message": "Not enough candle data for replay"})
        return

    from indicators import enrich_candles
    enrich_candles(candles)

    agent = Agent(
        agent_id=agent_data.get("id", "replay"),
        skills=agent_data.get("skills", {}),
        generation=agent_data.get("generation", 0),
    )

    capital = 100.0
    fee_rate = 0.0005
    spacing = agent.get_grid_spacing()
    levels = agent.get_grid_levels()
    stop_loss = agent.get_stop_loss()
    min_hold = agent.get_min_hold()

    grid = None
    equity = [100.0]
    trades = []
    total_fees = 0.0

    for i in range(1, len(candles)):
        c = candles[i]
        prev = candles[i - 1]
        price = c["close"]

        if grid is None:
            action = agent.decide(c, prev, None)
            if action == "buy" and capital > 1:
                size_per = capital / (levels * price)
                grid = {
                    "center": price, "capital": capital, "profit": 0.0, "rt": 0, "active": 0,
                    "buys": [{"price": price * (1 - spacing * n), "filled": False, "size": size_per} for n in range(1, levels + 1)],
                    "sells": [{"price": price * (1 + spacing * n), "filled": False, "size": size_per} for n in range(1, levels + 1)],
                }
                capital = 0
                trades.append({"action": "GRID START", "price": round(price, 2), "pnl": None, "candle_idx": i})
        else:
            grid["active"] += 1

            for buy in grid["buys"]:
                if not buy["filled"] and c["low"] <= buy["price"]:
                    buy["filled"] = True
                    fee = buy["size"] * buy["price"] * fee_rate
                    total_fees += fee
                    grid["profit"] -= fee
                    trades.append({"action": "BUY FILL", "price": round(buy["price"], 2), "pnl": None, "candle_idx": i})

            for j, sell in enumerate(grid["sells"]):
                if not sell["filled"] and c["high"] >= sell["price"]:
                    if j < len(grid["buys"]) and grid["buys"][j]["filled"]:
                        sell["filled"] = True
                        rt_profit = (sell["price"] - grid["buys"][j]["price"]) * sell["size"]
                        fee = sell["size"] * sell["price"] * fee_rate
                        total_fees += fee
                        grid["profit"] += rt_profit - fee
                        grid["rt"] += 1
                        grid["buys"][j]["filled"] = False
                        sell["filled"] = False
                        trades.append({"action": "ROUND TRIP", "price": round(sell["price"], 2), "pnl": round(rt_profit - fee, 3), "candle_idx": i})

            unrealized = sum((price - b["price"]) * b["size"] for b in grid["buys"] if b["filled"])
            total_pnl = grid["profit"] + unrealized
            total_pnl_pct = total_pnl / grid["capital"]

            if total_pnl_pct < -stop_loss:
                close_val = grid["capital"] + total_pnl
                capital = max(0, close_val)
                trades.append({"action": "GRID STOP", "price": round(price, 2), "pnl": round(total_pnl, 2), "candle_idx": i})
                grid = None
            elif grid["active"] >= min_hold:
                action = agent.decide(c, prev, {"entry": grid["center"], "peak": grid["center"]})
                if action == "sell":
                    close_val = grid["capital"] + total_pnl
                    capital = max(0, close_val)
                    trades.append({"action": "GRID CLOSE", "price": round(price, 2), "pnl": round(grid["profit"], 2), "candle_idx": i})
                    grid = None

        if grid:
            unrealized = sum((price - b["price"]) * b["size"] for b in grid["buys"] if b["filled"])
            eq = grid["capital"] + grid["profit"] + unrealized
        else:
            eq = capital
        equity.append(round(eq, 2))

    if grid:
        last_p = candles[-1]["close"]
        unrealized = sum((last_p - b["price"]) * b["size"] for b in grid["buys"] if b["filled"])
        capital = grid["capital"] + grid["profit"] + unrealized

    final_pnl = round(capital - 100.0, 4) if capital > 0 else round(equity[-1] - 100.0, 4)

    if len(equity) > 300:
        step = len(equity) // 300
        equity = equity[::step]

    await broadcast({
        "type": "replay_result",
        "agent": agent_data,
        "pnl": final_pnl,
        "trades": trades,
        "equity": equity,
        "candles": len(candles),
        "symbol": symbol,
        "total_fees": round(total_fees, 4),
    })


async def handler(ws):
    clients.add(ws)
    try:
        async for msg in ws:
            data = json.loads(msg)
            if data.get("type") == "start":
                asyncio.create_task(run_evolution(data.get("config", {})))
            elif data.get("type") == "replay":
                asyncio.create_task(replay_agent(data))
            elif data.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
    except websockets.ConnectionClosed:
        pass
    finally:
        clients.discard(ws)

async def main():
    print(f"Darwin server on ws://localhost:{PORT}")
    async with websockets.serve(handler, "localhost", PORT):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
