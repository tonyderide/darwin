#!/usr/bin/env python3
"""
Brute-force optimizer — test all skill combinations to find the optimal agent.

Phase 1: Test each skill solo → rank top N
Phase 2: Test all combos of 3-6 from top N → find the best
Phase 3: Weight optimization on the winning combo

Usage:
    python bruteforce.py --pair SOLUSD --interval 1 --top 15
    python bruteforce.py --pair SOLUSD --interval 240 --top 12
"""
import argparse
import json
import sys
import time
from itertools import combinations
from datetime import datetime
from pathlib import Path

from agent import Agent, SKILL_POOL
from arena import Arena
from data import fetch_ohlc
from tick_fetcher import load_ticks, ticks_to_candles, DATA_DIR

TICK_PAIRS = {
    "PF_SOLUSD": "SOLUSD", "PF_DOTUSD": "DOTUSD", "PF_ADAUSD": "ADAUSD",
    "PF_ETHUSD": "ETHUSD", "PF_XBTUSD": "XXBTZUSD",
}


def get_candles(symbol, interval, date_from=None, date_to=None):
    tick_pair = TICK_PAIRS.get(symbol, symbol.replace("PF_", ""))
    tick_file = DATA_DIR / f"{tick_pair}.jsonl"
    if tick_file.exists():
        ticks = load_ticks(tick_pair, date_from, date_to)
        if len(ticks) > 100:
            return ticks_to_candles(ticks, interval * 60)
    return fetch_ohlc(symbol, interval=interval, count=8760)


def evaluate_skills(skills_dict, candles_train, candles_test):
    """Evaluate a skill set on train and test data. Returns (train_pnl, test_pnl)."""
    agent = Agent("test", skills_dict)
    train_arena = Arena(candles=candles_train)
    train_arena.evaluate([agent])
    train_pnl = agent.fitness

    test_arena = Arena(candles=candles_test)
    test_arena.evaluate([agent])
    test_pnl = agent.fitness

    return train_pnl, test_pnl


def phase1_solo(all_skills, candles_train, candles_test):
    """Test each skill individually."""
    print("\n═══ PHASE 1: Solo skill ranking ═══\n")
    results = []
    for i, skill_name in enumerate(sorted(all_skills)):
        skills = {skill_name: 1.0}
        train_pnl, test_pnl = evaluate_skills(skills, candles_train, candles_test)
        results.append({
            "skill": skill_name,
            "train": round(train_pnl, 2),
            "test": round(test_pnl, 2),
            "total": round(train_pnl + test_pnl, 2),
        })
        sys.stdout.write(f"\r  [{i+1}/{len(all_skills)}] {skill_name:40s} train:{train_pnl:+7.2f} test:{test_pnl:+7.2f}")
        sys.stdout.flush()

    print("\n")
    results.sort(key=lambda x: x["total"], reverse=True)

    print(f"  {'Skill':40s} {'Train':>8s} {'Test':>8s} {'Total':>8s}")
    print(f"  {'─'*40} {'─'*8} {'─'*8} {'─'*8}")
    for r in results:
        marker = " ★" if r["total"] > 0 else ""
        print(f"  {r['skill']:40s} {r['train']:+8.2f} {r['test']:+8.2f} {r['total']:+8.2f}{marker}")

    return results


def phase2_combos(top_skills, candles_train, candles_test, min_size=3, max_size=6):
    """Test all combinations of top skills."""
    skill_names = [s["skill"] for s in top_skills]
    total_combos = sum(
        len(list(combinations(skill_names, k)))
        for k in range(min_size, max_size + 1)
    )
    print(f"\n═══ PHASE 2: Brute-force {total_combos:,} combinations ({min_size}-{max_size} skills from top {len(skill_names)}) ═══\n")

    best = {"skills": {}, "train": -999, "test": -999, "total": -999}
    results = []
    count = 0
    t0 = time.time()

    for k in range(min_size, max_size + 1):
        for combo in combinations(skill_names, k):
            skills = {s: 0.8 for s in combo}
            train_pnl, test_pnl = evaluate_skills(skills, candles_train, candles_test)
            total = train_pnl + test_pnl
            count += 1

            if total > best["total"]:
                best = {
                    "skills": skills,
                    "train": round(train_pnl, 2),
                    "test": round(test_pnl, 2),
                    "total": round(total, 2),
                    "combo": list(combo),
                }
                results.append(best.copy())

            if count % 100 == 0:
                elapsed = time.time() - t0
                rate = count / elapsed
                eta = (total_combos - count) / rate if rate > 0 else 0
                sys.stdout.write(f"\r  [{count:>7,}/{total_combos:,}] {rate:.0f}/s | ETA {eta:.0f}s | Best: {best['total']:+.2f} ({len(best.get('combo',[]))} skills)")
                sys.stdout.flush()

    print(f"\n\n  Tested {count:,} combinations in {time.time()-t0:.1f}s")
    return best, results


def phase3_weights(best_combo, candles_train, candles_test, steps=10):
    """Optimize weights for the winning combo."""
    print(f"\n═══ PHASE 3: Weight optimization on {len(best_combo)} skills ═══\n")
    skill_names = list(best_combo)

    best = {"skills": {}, "total": -999}
    count = 0

    # Test weight variations: 0.3, 0.5, 0.7, 0.9, 1.0 for each skill
    weights = [0.3, 0.5, 0.7, 0.9, 1.0]

    if len(skill_names) <= 4:
        # Full grid search for ≤4 skills
        from itertools import product
        weight_combos = list(product(weights, repeat=len(skill_names)))
    else:
        # Random sampling for >4 skills
        import random
        random.seed(42)
        weight_combos = [
            tuple(random.choice(weights) for _ in skill_names)
            for _ in range(5000)
        ]

    total = len(weight_combos)
    for wc in weight_combos:
        skills = {name: w for name, w in zip(skill_names, wc)}
        train_pnl, test_pnl = evaluate_skills(skills, candles_train, candles_test)
        tot = train_pnl + test_pnl
        count += 1

        if tot > best["total"]:
            best = {
                "skills": {k: round(v, 2) for k, v in skills.items()},
                "train": round(train_pnl, 2),
                "test": round(test_pnl, 2),
                "total": round(tot, 2),
            }

        if count % 50 == 0:
            sys.stdout.write(f"\r  [{count}/{total}] Best: {best['total']:+.2f}")
            sys.stdout.flush()

    print(f"\n")
    return best


def main():
    parser = argparse.ArgumentParser(description="Brute-force skill optimizer")
    parser.add_argument("--symbol", default="PF_SOLUSD")
    parser.add_argument("--interval", type=int, default=1, help="Candle interval in minutes")
    parser.add_argument("--top", type=int, default=15, help="Top N skills for phase 2")
    parser.add_argument("--date-from", default=None)
    parser.add_argument("--date-to", default=None)
    parser.add_argument("--max-combo", type=int, default=6, help="Max skills per combo")
    args = parser.parse_args()

    print(f"Loading {args.symbol} data ({args.interval}min)...")
    candles = get_candles(args.symbol, args.interval, args.date_from, args.date_to)
    print(f"Got {len(candles):,} candles")

    if len(candles) < 200:
        print("Not enough data!")
        return

    # Split 60/40: train on first 60%, test on last 40%
    split = int(len(candles) * 0.6)
    train = candles[:split]
    test = candles[split:]
    print(f"Train: {len(train):,} candles | Test: {len(test):,} candles")

    # Phase 1
    solo_results = phase1_solo(list(SKILL_POOL.keys()), train, test)
    top_skills = [r for r in solo_results if r["total"] > -5][:args.top]
    print(f"\nTop {len(top_skills)} skills selected for phase 2")

    # Phase 2
    best_combo, _ = phase2_combos(top_skills, train, test, min_size=3, max_size=args.max_combo)

    print(f"\n  ★ BEST COMBO: {best_combo['combo']}")
    print(f"    Train: {best_combo['train']:+.2f} | Test: {best_combo['test']:+.2f} | Total: {best_combo['total']:+.2f}")

    # Phase 3
    optimized = phase3_weights(best_combo["combo"], train, test)

    print(f"  ★ OPTIMIZED AGENT:")
    print(f"    Train: {optimized['train']:+.2f} | Test: {optimized['test']:+.2f} | Total: {optimized['total']:+.2f}")
    print(f"    Skills:")
    for name, weight in sorted(optimized["skills"].items(), key=lambda x: -x[1]):
        print(f"      {name:40s} {int(weight*100)}")

    # Save result
    result_file = Path(__file__).parent / "data" / "optimal_agent.json"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    with open(result_file, "w") as f:
        json.dump({
            "symbol": args.symbol,
            "interval": args.interval,
            "candles": len(candles),
            "train_size": len(train),
            "test_size": len(test),
            "timestamp": datetime.now().isoformat(),
            "solo_ranking": solo_results[:20],
            "best_combo": best_combo,
            "optimized": optimized,
        }, f, indent=2)
    print(f"\n  Saved to {result_file}")


if __name__ == "__main__":
    main()
