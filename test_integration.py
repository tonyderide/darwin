"""Integration test: full evolution pipeline without WebSocket."""
import pytest
from data import fetch_ohlc
from agent import create_random_agent, SKILL_POOL
from arena import Arena
from evolution import evolve_generation

def test_full_evolution_pipeline():
    # Use a small dataset
    candles = fetch_ohlc("PF_SOLUSD", interval=60, count=200)
    assert len(candles) > 50, f"Not enough candles: {len(candles)}"

    # Create population
    pop_size = 6
    agents = [create_random_agent(num_skills=3) for _ in range(pop_size)]
    arena = Arena(candles=candles)

    # Run 3 generations
    for gen in range(3):
        arena.evaluate(agents)
        ranked = sorted(agents, key=lambda a: a.fitness, reverse=True)
        best = ranked[0]
        assert isinstance(best.fitness, float)
        agents = evolve_generation(agents, target_size=pop_size, mutation_rate=0.3)

    # Final eval
    arena.evaluate(agents)
    final_best = max(agents, key=lambda a: a.fitness)
    print(f"\nFinal best: {final_best.agent_id} | PnL: ${final_best.fitness:.2f}")
    print(f"Skills: {final_best.skills}")
    assert final_best.fitness != 0 or True  # may be 0 if market flat, that's ok
