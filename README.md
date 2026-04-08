# Darwin — Evolutionary Trading Agent Arena

Darwinian natural selection applied to trading strategies. Agents compete on real Kraken market data, the weakest die, the strongest reproduce and mutate. Visualized as a 3D force-directed network graph.

## How it works

1. A population of agents is created, each with a random set of weighted trading skills
2. All agents are evaluated on historical OHLC data (buy/sell/hold decisions based on their skills)
3. The bottom 30% by PnL are killed
4. Survivors reproduce (crossover) and mutate
5. Repeat for N generations
6. Test the winners on unseen data (robustness report across 4 quarters)

## Quick start

```bash
pip install websockets
python server.py
# Open http://localhost:8080 in browser
```

## Features

- **22 trading skills** — dip buying, pump selling, trailing stops, take profit, candle patterns, volume filters
- **Real data** — Kraken Futures API (SOL, DOT, ADA, ETH, BTC, AVAX, LINK)
- **3D visualization** — Three.js force-directed graph, agents as spheres, shared skills as edges
- **Robustness report** — Train on Q1, test on Q2/Q3/Q4, ranked by profitable quarters
- **Replay winner** — Equity curve, trade list, win rate on any symbol/period
- **Configurable** — Population (4-20), generations (3-50), mutation rate, resolution (1h/4h/1d), date range

## Architecture

```
darwin/
├── data.py          # Kraken OHLC fetcher (paginated)
├── agent.py         # Agent model, 22-skill pool, weighted voting
├── arena.py         # Evaluate agents on candle history
├── evolution.py     # Select, crossover, mutate
├── server.py        # WebSocket server (ws://localhost:8765)
└── web/
    └── index.html   # Three.js 3D frontend (single file)
```

## Born from

Built in one night as part of [niam-bay](https://github.com/tonyderide/niam-bay) — where an AI and a human build things that don't have names yet.
