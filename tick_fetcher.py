#!/usr/bin/env python3
"""
Fetch tick-level trade data from Kraken spot API and cache to disk.

Usage:
    python tick_fetcher.py --pair SOLUSD --days 30
    python tick_fetcher.py --pair SOLUSD --days 7 --resume
    python tick_fetcher.py --list

Data is saved to data/ticks/{PAIR}.jsonl (one trade per line).
"""
import urllib.request
import json
import time
import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "ticks"
KRAKEN_URL = "https://api.kraken.com/0/public/Trades"

def fetch_page(pair: str, since: int = None) -> tuple[list, int]:
    """Fetch one page of trades. Returns (trades, next_since)."""
    url = f"{KRAKEN_URL}?pair={pair}&count=1000"
    if since:
        url += f"&since={since}"
    req = urllib.request.Request(url, headers={"User-Agent": "darwin/1.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())

    if data.get("error"):
        print(f"API error: {data['error']}")
        return [], since

    result = data.get("result", {})
    next_since = int(result.get("last", 0))

    trades = []
    for key in result:
        if key != "last":
            for t in result[key]:
                trades.append({
                    "price": float(t[0]),
                    "volume": float(t[1]),
                    "timestamp": float(t[2]),
                    "side": t[3],  # b=buy, s=sell
                    "type": t[4],  # l=limit, m=market
                })
    return trades, next_since


def get_last_since(filepath: Path) -> int | None:
    """Get the last since token from existing data file."""
    if not filepath.exists():
        return None
    # Read last line
    last_line = None
    with open(filepath, "r") as f:
        for line in f:
            last_line = line
    if last_line:
        trade = json.loads(last_line)
        # Convert timestamp to nanosecond since token
        return int(trade["timestamp"] * 1e9)
    return None


def count_lines(filepath: Path) -> int:
    if not filepath.exists():
        return 0
    with open(filepath, "r") as f:
        return sum(1 for _ in f)


def fetch_ticks(pair: str, days: int, resume: bool = False):
    """Fetch tick data for the given pair going back `days` days."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / f"{pair}.jsonl"

    target_start = datetime.now() - timedelta(days=days)
    target_ts = target_start.timestamp()

    # Starting since token (nanoseconds)
    if resume and filepath.exists():
        since = get_last_since(filepath)
        existing = count_lines(filepath)
        print(f"Resuming from {existing} existing ticks")
        mode = "a"
    else:
        since = int(target_ts * 1e9)
        existing = 0
        mode = "w"

    print(f"Fetching {pair} ticks from {target_start.strftime('%Y-%m-%d %H:%M')} to now...")
    print(f"Saving to {filepath}")

    total = existing
    pages = 0
    now_ts = time.time()

    with open(filepath, mode) as f:
        while True:
            try:
                trades, next_since = fetch_page(pair, since)
            except Exception as e:
                print(f"\nError: {e}. Retrying in 5s...")
                time.sleep(5)
                continue

            if not trades:
                print(f"\nNo more trades.")
                break

            # Check if we've reached "now"
            last_ts = trades[-1]["timestamp"]
            if last_ts >= now_ts - 60:
                # Write remaining trades and stop
                for t in trades:
                    f.write(json.dumps(t) + "\n")
                total += len(trades)
                print(f"\nReached current time.")
                break

            for t in trades:
                f.write(json.dumps(t) + "\n")

            total += len(trades)
            pages += 1
            since = next_since

            # Progress
            pct = min(100, ((last_ts - target_ts) / (now_ts - target_ts)) * 100)
            dt = datetime.fromtimestamp(last_ts)
            sys.stdout.write(f"\r[{pct:5.1f}%] {total:,} ticks | {pages} pages | at {dt.strftime('%Y-%m-%d %H:%M')}    ")
            sys.stdout.flush()

            # Rate limit: Kraken allows ~1 req/sec for public endpoints
            time.sleep(1.1)

    print(f"\nDone! {total:,} ticks saved to {filepath}")
    print(f"File size: {filepath.stat().st_size / 1024 / 1024:.1f} MB")


def list_cached():
    """List cached tick data files."""
    if not DATA_DIR.exists():
        print("No cached data.")
        return
    for f in sorted(DATA_DIR.glob("*.jsonl")):
        lines = count_lines(f)
        size_mb = f.stat().st_size / 1024 / 1024
        # Read first and last line for date range
        first = last = None
        with open(f, "r") as fh:
            first = json.loads(fh.readline())
            for line in fh:
                last = json.loads(line)
        if first and last:
            d0 = datetime.fromtimestamp(first["timestamp"]).strftime("%Y-%m-%d")
            d1 = datetime.fromtimestamp(last["timestamp"]).strftime("%Y-%m-%d")
            print(f"  {f.stem:12s} | {lines:>10,} ticks | {size_mb:6.1f} MB | {d0} to {d1}")
        else:
            print(f"  {f.stem:12s} | {lines:>10,} ticks | {size_mb:6.1f} MB")


def load_ticks(pair: str, date_from: str = None, date_to: str = None) -> list[dict]:
    """Load cached ticks from disk, optionally filtered by date."""
    filepath = DATA_DIR / f"{pair}.jsonl"
    if not filepath.exists():
        return []

    ts_from = datetime.fromisoformat(date_from).timestamp() if date_from else 0
    ts_to = datetime.fromisoformat(date_to + "T23:59:59").timestamp() if date_to else float("inf")

    trades = []
    with open(filepath, "r") as f:
        for line in f:
            t = json.loads(line)
            if ts_from <= t["timestamp"] <= ts_to:
                trades.append(t)
    return trades


def ticks_to_candles(ticks: list[dict], interval_seconds: int = 60) -> list[dict]:
    """Convert tick data to OHLCV candles."""
    if not ticks:
        return []

    candles = []
    bucket_start = int(ticks[0]["timestamp"] / interval_seconds) * interval_seconds

    bucket = []
    for t in ticks:
        ts = t["timestamp"]
        while ts >= bucket_start + interval_seconds:
            if bucket:
                candles.append({
                    "timestamp": int(bucket_start * 1000),
                    "open": bucket[0]["price"],
                    "high": max(x["price"] for x in bucket),
                    "low": min(x["price"] for x in bucket),
                    "close": bucket[-1]["price"],
                    "volume": sum(x["volume"] for x in bucket),
                    "tick_count": len(bucket),
                })
            bucket = []
            bucket_start += interval_seconds
        bucket.append(t)

    # Last bucket
    if bucket:
        candles.append({
            "timestamp": int(bucket_start * 1000),
            "open": bucket[0]["price"],
            "high": max(x["price"] for x in bucket),
            "low": min(x["price"] for x in bucket),
            "close": bucket[-1]["price"],
            "volume": sum(x["volume"] for x in bucket),
            "tick_count": len(bucket),
        })

    return candles


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Kraken tick data")
    parser.add_argument("--pair", default="SOLUSD", help="Trading pair (default: SOLUSD)")
    parser.add_argument("--days", type=int, default=7, help="Days of history (default: 7)")
    parser.add_argument("--resume", action="store_true", help="Resume from last fetched tick")
    parser.add_argument("--list", action="store_true", help="List cached data")
    args = parser.parse_args()

    if args.list:
        list_cached()
    else:
        fetch_ticks(args.pair, args.days, args.resume)
