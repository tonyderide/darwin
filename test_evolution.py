import pytest
from agent import Agent, SKILL_POOL
from evolution import select_survivors, crossover, mutate, evolve_generation

def test_select_kills_bottom_30pct():
    agents = [Agent(f"a{i}", {"buy-on-dip-3pct": 0.5}) for i in range(10)]
    for i, a in enumerate(agents):
        a.fitness = float(i)
    survivors = select_survivors(agents, kill_ratio=0.3)
    assert len(survivors) == 7
    assert all(s.alive for s in survivors)

def test_crossover_produces_child():
    p1 = Agent("p1", {"buy-on-dip-3pct": 0.8, "trailing-stop-2pct": 0.6})
    p2 = Agent("p2", {"sell-on-pump-3pct": 0.9, "hold-in-low-volume": 0.5})
    child = crossover(p1, p2, generation=1)
    assert child.generation == 1
    assert len(child.skills) > 0
    assert set(child.parent_ids) == {"p1", "p2"}

def test_mutate_changes_skills():
    a = Agent("m1", {"buy-on-dip-3pct": 0.5, "trailing-stop-2pct": 0.5})
    mutated = mutate(a, rate=1.0)
    assert mutated.skills != {"buy-on-dip-3pct": 0.5, "trailing-stop-2pct": 0.5} or len(mutated.skills) != 2

def test_evolve_generation():
    agents = [Agent(f"a{i}", {"buy-on-dip-3pct": 0.5, "sell-on-pump-3pct": 0.3}) for i in range(6)]
    for i, a in enumerate(agents):
        a.fitness = float(i)
    next_gen = evolve_generation(agents, target_size=6, kill_ratio=0.3, mutation_rate=0.3)
    assert len(next_gen) == 6
