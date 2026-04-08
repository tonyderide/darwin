"""WebSocket server — runs evolution and pushes events to frontend."""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import websockets

from data import fetch_ohlc
from agent import Agent, create_random_agent, SKILL_POOL
from arena import Arena
from evolution import select_survivors, evolve_generation


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

    await broadcast({"type": "status", "message": f"Fetching {symbol} data ({interval}min candles)..."})
    all_candles = fetch_ohlc(symbol, interval=interval, count=8760)

    # Apply date filter if provided
    if date_from or date_to:
        all_candles = filter_candles_by_date(all_candles, date_from, date_to)
        await broadcast({"type": "status", "message": f"Filtered to {len(all_candles)} candles ({date_from or 'start'} → {date_to or 'end'})"})

    if not all_candles or len(all_candles) < 400:
        await broadcast({"type": "error", "message": "No candle data"})
        return

    # Split into 4 quarters (Q1=oldest, Q4=most recent)
    q_size = len(all_candles) // 4
    quarters = [
        all_candles[i * q_size : (i + 1) * q_size]
        for i in range(4)
    ]
    quarter_labels = ["Q1 (oldest)", "Q2", "Q3", "Q4 (recent)"]

    # Train on Q1 (oldest 3 months)
    train_candles = quarters[0]

    await broadcast({"type": "status", "message": f"Got {len(all_candles)} candles. 4 quarters of ~{q_size} candles. Training on {quarter_labels[0]}..."})

    agents = [create_random_agent(num_skills=4, generation=0) for _ in range(pop_size)]
    arena = Arena(candles=train_candles)

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

        test_arena = Arena(candles=quarters[qi])
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
    """Replay a single agent on specified data, return detailed trades."""
    agent_data = config.get("agent", {})
    symbol = config.get("symbol", "PF_SOLUSD")
    date_from = config.get("date_from")
    date_to = config.get("date_to")

    interval = config.get("interval", 240)

    await broadcast({"type": "status", "message": f"Replaying {agent_data.get('id', '?')} on {symbol}..."})

    candles = fetch_ohlc(symbol, interval=interval, count=8760)
    if date_from or date_to:
        candles = filter_candles_by_date(candles, date_from, date_to)

    if not candles or len(candles) < 10:
        await broadcast({"type": "error", "message": "Not enough candle data for replay"})
        return

    # Rebuild agent from skills
    agent = Agent(
        agent_id=agent_data.get("id", "replay"),
        skills=agent_data.get("skills", {}),
        generation=agent_data.get("generation", 0),
    )

    # Detailed replay with trade logging
    capital = 100.0
    position = None
    trades = []
    equity = [capital]

    for i in range(1, len(candles)):
        candle = candles[i]
        prev = candles[i - 1]
        action = agent.decide(candle, prev, position)

        if action == "buy" and position is None:
            size = capital / candle["close"]
            position = {"entry": candle["close"], "size": size, "peak": candle["close"]}
            trades.append({"action": "buy", "price": candle["close"], "pnl": None, "candle_idx": i})
            capital = 0

        elif action == "sell" and position is not None:
            capital = position["size"] * candle["close"]
            pnl_pct = ((candle["close"] - position["entry"]) / position["entry"]) * 100
            trades.append({"action": "sell", "price": candle["close"], "pnl": round(pnl_pct, 2), "candle_idx": i})
            position = None

        elif position is not None:
            if candle["close"] > position["peak"]:
                position["peak"] = candle["close"]

        # Track equity
        if position:
            eq = position["size"] * candle["close"]
        else:
            eq = capital
        equity.append(round(eq, 2))

    # Close open position
    if position is not None:
        capital = position["size"] * candles[-1]["close"]
        pnl_pct = ((candles[-1]["close"] - position["entry"]) / position["entry"]) * 100
        trades.append({"action": "sell (close)", "price": candles[-1]["close"], "pnl": round(pnl_pct, 2), "candle_idx": len(candles) - 1})

    final_pnl = round(capital - 100.0, 4) if capital > 0 else round(equity[-1] - 100.0, 4)

    # Downsample equity to ~200 points for the chart
    if len(equity) > 200:
        step = len(equity) // 200
        equity = equity[::step]

    await broadcast({
        "type": "replay_result",
        "agent": agent_data,
        "pnl": final_pnl,
        "trades": trades,
        "equity": equity,
        "candles": len(candles),
        "symbol": symbol,
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
