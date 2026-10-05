# FleetFlow — Decentralized Multi-Agent Coordination for Autonomous Logistics

KPIT K-Impact 2.0 project demo. The project models a city fleet where autonomous logistics vehicles coordinate **without a central controller**. The simulation focuses on the coordination layer rather than autonomous driving/perception.

## What is simulated

- **Task bidding / allocation:** Contract-Net style auction. Eligible vehicles bid using distance, battery and workload; the best bid wins.
- **Road movement:** Vehicles move through a 300×300 city grid and react to intersection and traffic conditions.
- **Multiple intersections:** I1–I4 negotiate access using arrival order and task priority.
- **GO / YIELD coordination:** A vehicle receives a coordinated GO while competing agents yield at a shared intersection.
- **Dynamic traffic:** Traffic levels change during the simulation and reduce movement speed near congested intersections.
- **Vehicle failure:** A failed vehicle releases its active/queued tasks. Cargo already picked up is converted into a new pickup point.
- **Self-healing re-auction:** Released tasks are immediately re-auctioned to another eligible vehicle.
- **Communication loss / recovery:** A disconnected vehicle cannot join new auctions, but its existing task continues locally; the link automatically recovers.
- **Emergency / human override:** A human can force a selected vehicle to receive GO priority at an intersection.
- **Live dashboard:** Vehicles, routes, tasks, traffic, intersection decisions, failures, re-auctions, communication events and overrides are visible in real time.

No LiDAR, computer vision, vehicle physics, reinforcement learning or external services are required.

## Run the live demo

Requires **Python 3.10+**. No pip install is required; the backend uses only the Python standard library.

```bash
python server.py
```

Open:

```text
http://localhost:8000
```

Press **Start**. The default **Scripted demo** automatically demonstrates:

1. task auctions and vehicle movement;
2. an intersection GO/YIELD decision;
3. V3 failure and task re-auction to another vehicle;
4. communication loss and recovery for V4;
5. a human emergency override at I2;
6. a heavy-traffic change at I2.

You can also use the dashboard controls to fail/repair vehicles, toggle communication, add tasks, change traffic and trigger/clear human override.

## Replay / CLI

Generate a 60-tick JSONL replay:

```bash
python run_demo.py --ticks 60 --out sample_states.jsonl
```

Run a random fleet instead:

```bash
python run_demo.py --random --ticks 100
```

Run the core engine directly:

```bash
python fleet_sim.py --ticks 45
```

## Architecture

```text
fleet_sim.py
   │
   ├── Vehicle agents
   ├── TaskAllocator (Contract Net)
   ├── IntersectionManager (GO / YIELD)
   ├── FailureHandler (re-auction / recovery)
   ├── Traffic + communication state
   └── Simulation.get_state()
              │
              ▼
          server.py
       HTTP JSON API
              │
              ▼
     index.html + app.js
       + styles.css
       Live SVG dashboard
```

The original standard-library architecture is retained. The browser is only a visualization/control client; the coordination decisions remain in the Python simulation engine.
