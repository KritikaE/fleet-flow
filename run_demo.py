"""
Demo runner for the decentralized logistics simulator.

Examples:
  python run_demo.py
  python run_demo.py --out states.jsonl
  python run_demo.py --delay 0.5
  python run_demo.py --random
"""
import argparse
import json
import time

from fleet_sim import Simulation


def scripted_demo() -> Simulation:
    """A deterministic story designed to expose every coordination behaviour."""
    sim = Simulation(num_vehicles=0, seed=42)
    for x, y, b in [
        (20, 100, 90), (100, 20, 95), (260, 200, 88),
        (200, 280, 80), (40, 200, 75), (200, 60, 99)
    ]:
        sim.add_vehicle(x, y, b, capacity=3)

    sim.add_task((30, 100), (180, 100), "normal")
    sim.add_task((100, 24), (100, 180), "high")
    sim.add_task((240, 200), (100, 200), "normal")
    sim.demo_mode = True
    return sim


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--out")
    ap.add_argument("--random", action="store_true")
    args = ap.parse_args()

    sim = Simulation(num_vehicles=6, seed=1, task_spawn_prob=0.15) if args.random else scripted_demo()
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    try:
        for _ in range(args.ticks):
            line = json.dumps(sim.tick())
            print(line, flush=True)
            if out:
                out.write(line + "\n")
            if args.delay:
                time.sleep(args.delay)
    finally:
        if out:
            out.close()


if __name__ == "__main__":
    main()
