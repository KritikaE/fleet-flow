"""
Decentralized Multi-Agent Coordination for Autonomous Logistics.

The core is intentionally lightweight: vehicles are agents, tasks are auctions,
intersections are negotiated shared resources, and the dashboard consumes only
Simulation.get_state(). No perception, vehicle physics or ML is required.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

Point = Tuple[float, float]

DEFAULT_SPEED = 10.0
DRAIN_PER_UNIT = 0.05
IDLE_RECHARGE = 0.5
BATTERY_RESERVE = 10.0
BATTERY_CRITICAL = 5.0

W_DIST = 1.0
W_BATTERY = 0.5
W_LOAD = 40.0

PRIORITY_RANK = {"emergency": -1, "high": 0, "normal": 1, "low": 2}


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def segment_point_distance(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg_len2))
    return dist(p, (a[0] + t * dx, a[1] + t * dy))


@dataclass
class Task:
    id: str
    pickup: Point
    dropoff: Point
    priority: str = "normal"
    status: str = "pending"
    assigned_to: Optional[str] = None
    created_at: float = 0.0
    picked_up: bool = False
    reassigned_from: Optional[str] = None
    no_bidder_logged: bool = False


@dataclass
class Bid:
    vehicle_id: str
    score: float
    distance: float
    battery: int
    workload: int


@dataclass
class Vehicle:
    id: str
    x: float
    y: float
    battery: float = 100.0
    capacity: int = 3
    status: str = "idle"
    current_task: Optional[str] = None
    destination: Optional[Point] = None
    task_queue: List[str] = field(default_factory=list)
    phase: Optional[str] = None
    failure_reason: Optional[str] = None
    connected: bool = True
    comm_loss_remaining: int = 0
    traffic_delay: float = 0.0
    route: List[Point] = field(default_factory=list)
    route_index: int = 0

    @property
    def pos(self) -> Point:
        return (self.x, self.y)

    @property
    def workload(self) -> int:
        return len(self.task_queue) + (1 if self.current_task else 0)

    def committed_route(self, tasks: Dict[str, Task]) -> Tuple[float, Point]:
        pos, total = self.pos, 0.0
        if self.current_task:
            t = tasks[self.current_task]
            if self.phase == "to_pickup":
                total += dist(pos, t.pickup) + dist(t.pickup, t.dropoff)
            else:
                total += dist(pos, t.dropoff)
            pos = t.dropoff
        for tid in self.task_queue:
            t = tasks[tid]
            total += dist(pos, t.pickup) + dist(t.pickup, t.dropoff)
            pos = t.dropoff
        return total, pos

    def bid_for(self, task: Task, tasks: Dict[str, Task]) -> Optional[Bid]:
        # A disconnected agent cannot participate in a new auction.
        if self.status == "failed" or not self.connected or self.workload >= self.capacity:
            return None
        committed, end_pos = self.committed_route(tasks)
        approach = dist(end_pos, task.pickup)
        trip = dist(task.pickup, task.dropoff)
        needed = (committed + approach + trip) * DRAIN_PER_UNIT
        if self.battery - needed < BATTERY_RESERVE:
            return None
        priority_bonus = -25.0 if task.priority == "emergency" else (-8.0 if task.priority == "high" else 0.0)
        score = (W_DIST * (committed + approach)
                 + W_BATTERY * (100.0 - self.battery)
                 + W_LOAD * self.workload + priority_bonus)
        return Bid(self.id, round(score, 2), round(committed + approach, 1),
                   round(self.battery), self.workload)

    def start_next_task(self, tasks: Dict[str, Task]) -> None:
        if self.current_task or not self.task_queue:
            return
        tid = min(self.task_queue,
                  key=lambda i: (PRIORITY_RANK[tasks[i].priority], tasks[i].created_at, i))
        self.task_queue.remove(tid)
        self.current_task = tid
        self.phase = "to_pickup"
        self.destination = tasks[tid].pickup
        self.route = [self.destination]
        self.route_index = 0
        self.status = "delivering"


@dataclass
class Intersection:
    id: str
    x: float
    y: float
    radius: float = 12.0

    @property
    def center(self) -> Point:
        return (self.x, self.y)

    def contains(self, x: float, y: float) -> bool:
        return math.hypot(x - self.x, y - self.y) <= self.radius


class TaskAllocator:
    def __init__(self, sim: "Simulation"):
        self.sim = sim

    def collect_bids(self, task: Task) -> List[Bid]:
        bids = [b for v in self.sim.vehicles.values()
                if (b := v.bid_for(task, self.sim.tasks)) is not None]
        return sorted(bids, key=lambda b: (b.score, b.vehicle_id))

    def allocate(self, task: Task) -> Optional[str]:
        bids = self.collect_bids(task)
        if not bids:
            return None
        win = bids[0]
        v = self.sim.vehicles[win.vehicle_id]
        task.status, task.assigned_to = "assigned", v.id
        task.no_bidder_logged = False
        v.task_queue.append(task.id)
        v.start_next_task(self.sim.tasks)
        if v.current_task:
            self.sim._set_route(v, v.destination)
        detail = (f"best of {len(bids)} bid(s): score {win.score}, {win.distance} units away, "
                  f"battery {win.battery}%, load {win.workload}/{v.capacity}")
        if task.reassigned_from:
            self.sim.log_event(
                f"Task {task.id} re-auctioned: {task.reassigned_from} → {v.id} ({detail})",
                "heal")
            task.reassigned_from = None
        else:
            self.sim.log_event(f"Vehicle {v.id} won Task {task.id} ({detail})", "auction")
        return v.id

    def allocate_pending(self) -> None:
        pending = [t for t in self.sim.tasks.values() if t.status == "pending"]
        pending.sort(key=lambda t: (PRIORITY_RANK[t.priority], t.created_at, t.id))
        for t in pending:
            if self.allocate(t) is None and not t.no_bidder_logged:
                t.no_bidder_logged = True
                self.sim.log_event(f"Task {t.id} has no eligible bidder; waiting in pool", "auction")


class IntersectionManager:
    """Negotiates a shared intersection resource using arrival + priority."""

    def __init__(self, sim: "Simulation", intersections: List[Intersection]):
        self.sim = sim
        self.intersections = intersections
        self.arrivals: Dict[Tuple[str, str], float] = {}
        self.waiting: Dict[Tuple[str, str], float] = {}
        self.granted: Set[Tuple[str, str]] = set()

    def resolve(self, requests: List[Tuple[Vehicle, Intersection, float]],
                vehicles: Iterable[Vehicle], now: float) -> Set[str]:
        yielding: Set[str] = set()
        live: Set[Tuple[str, str]] = set()
        by_inter: Dict[str, List[Tuple[Vehicle, Intersection]]] = {}
        for v, inter, eta in requests:
            key = (v.id, inter.id)
            live.add(key)
            self.arrivals.setdefault(key, eta)
            by_inter.setdefault(inter.id, []).append((v, inter))

        vehicles = list(vehicles)
        for iid, reqs in by_inter.items():
            inter = reqs[0][1]
            reqs.sort(key=lambda r: (
                0 if self.sim.emergency_override.get(iid) == r[0].id else 1,
                PRIORITY_RANK.get(self.sim.tasks[r[0].current_task].priority, 1)
                if r[0].current_task in self.sim.tasks else 1,
                self.arrivals[(r[0].id, iid)], r[0].id
            ))
            occupants = [o for o in vehicles
                         if o.status != "failed" and o.destination is not None
                         and inter.contains(o.x, o.y)]

            forced = self.sim.emergency_override.get(iid)
            winner = forced if forced and any(v.id == forced for v, _ in reqs) else (
                None if occupants else reqs[0][0].id
            )

            for v, _ in reqs:
                key = (v.id, iid)
                if v.id == winner:
                    if key not in self.granted:
                        self.granted.add(key)
                        if self.sim.emergency_override.get(iid) == v.id:
                            self.sim.log_event(
                                f"Human override: {iid} forced GO for {v.id}", "override")
                        else:
                            priority = self.sim.tasks[v.current_task].priority if v.current_task in self.sim.tasks else "normal"
                            self.sim.log_event(
                                f"Vehicle {v.id} GO at {iid} ({priority} priority / coordinated grant)", "intersection")
                    if key in self.waiting:
                        self.sim.log_event(
                            f"Vehicle {v.id} cleared to enter {iid} after waiting "
                            f"{now - self.waiting.pop(key):.1f} time unit(s)", "intersection")
                    continue

                yielding.add(v.id)
                if key not in self.waiting:
                    self.waiting[key] = now
                    if forced:
                        why = f"human override gives {forced} priority"
                    elif occupants:
                        why = f"intersection occupied by {occupants[0].id}"
                    else:
                        why = f"{winner} has coordination priority"
                    self.sim.log_event(f"Vehicle {v.id} YIELD at {iid} ({why})", "intersection")

        for key in list(self.arrivals):
            if key not in live:
                self.arrivals.pop(key, None)
                self.waiting.pop(key, None)
                self.granted.discard(key)
        return yielding

    def forget(self, vehicle_id: str) -> None:
        for d in (self.arrivals, self.waiting):
            for key in [k for k in d if k[0] == vehicle_id]:
                del d[key]


class FailureHandler:
    REASONS = {
        "breakdown": "mechanical breakdown",
        "battery_critical": "battery critical",
        "blocked": "path blocked",
    }

    def __init__(self, sim: "Simulation"):
        self.sim = sim

    def fail_vehicle(self, vehicle_id: str, mode: str = "breakdown") -> bool:
        sim = self.sim
        if vehicle_id not in sim.vehicles:
            return False
        v = sim.vehicles[vehicle_id]
        if v.status == "failed":
            return False
        if mode not in self.REASONS:
            raise ValueError(f"mode must be one of {sorted(self.REASONS)}")
        if mode == "battery_critical":
            v.battery = min(v.battery, 2.0)
        v.status, v.failure_reason = "failed", self.REASONS[mode]
        sim.log_event(f"Vehicle {v.id} FAILED ({v.failure_reason})", "fault")

        orphaned = ([v.current_task] if v.current_task else []) + list(v.task_queue)
        v.current_task, v.destination, v.phase, v.task_queue = None, None, None, []
        v.route, v.route_index = [], 0
        sim.intersections.forget(v.id)

        released: List[Task] = []
        for tid in orphaned:
            t = sim.tasks[tid]
            if t.picked_up:
                t.pickup = (round(v.x, 1), round(v.y, 1))
                t.picked_up = False
                sim.log_event(
                    f"Task {tid} cargo stranded at ({t.pickup[0]}, {t.pickup[1]}); "
                    "new pickup point set", "heal")
            t.status, t.assigned_to, t.reassigned_from = "pending", None, v.id
            t.no_bidder_logged = False
            released.append(t)

        released.sort(key=lambda t: (PRIORITY_RANK[t.priority], t.created_at, t.id))
        for t in released:
            if sim.allocator.allocate(t) is None:
                t.no_bidder_logged = True
                sim.log_event(
                    f"Task {t.id} has no eligible bidder after {v.id} failed; waiting in pool",
                    "heal")
        return True

    def check_battery(self, v: Vehicle) -> None:
        if v.status != "failed" and v.current_task and v.battery <= BATTERY_CRITICAL:
            self.fail_vehicle(v.id, "battery_critical")

    def repair_vehicle(self, vehicle_id: str, battery: float = 100.0) -> None:
        if vehicle_id not in self.sim.vehicles:
            return
        v = self.sim.vehicles[vehicle_id]
        if v.status == "failed":
            v.status, v.failure_reason, v.battery = "idle", None, battery
            v.connected = True
            v.comm_loss_remaining = 0
            self.sim.log_event(f"Vehicle {v.id} repaired and back in service", "heal")


class Simulation:
    def __init__(self, num_vehicles: int = 6, grid_size: Tuple[int, int] = (300, 300),
                 seed: Optional[int] = None, task_spawn_prob: float = 0.0,
                 speed: float = DEFAULT_SPEED, dt: float = 1.0,
                 intersections: Optional[List[Intersection]] = None):
        self.rng = random.Random(seed)
        self.grid_w, self.grid_h = grid_size
        self.speed, self.dt = speed, dt
        self.task_spawn_prob = task_spawn_prob
        self.time = 0.0
        self.completed_count = 0
        self.vehicles: Dict[str, Vehicle] = {}
        self.tasks: Dict[str, Task] = {}
        self.log: List[dict] = []
        self._task_counter = 0
        self.event_counts = {"auctions": 0, "yields": 0, "failures": 0,
                             "reauctions": 0, "comm_losses": 0, "overrides": 0}
        self.traffic = {
            "I1": {"level": 0.25, "label": "Light", "speed_factor": 0.90},
            "I2": {"level": 0.45, "label": "Moderate", "speed_factor": 0.78},
            "I3": {"level": 0.20, "label": "Light", "speed_factor": 0.92},
            "I4": {"level": 0.35, "label": "Moderate", "speed_factor": 0.84},
        }
        self.emergency_override: Dict[str, str] = {}
        self.demo_mode = False
        self._demo_fired: Set[str] = set()

        default_intersections = [
            Intersection("I1", 100, 100),
            Intersection("I2", 200, 200),
            Intersection("I3", 200, 100),
            Intersection("I4", 100, 200),
        ]
        self.intersection_manager = IntersectionManager(
            self, intersections or default_intersections)
        # Backwards-compatible alias used by the original code.
        self.intersections = self.intersection_manager

        for _ in range(num_vehicles):
            self.add_vehicle(
                x=self.rng.uniform(15, self.grid_w - 15),
                y=self.rng.uniform(15, self.grid_h - 15),
                battery=self.rng.uniform(70, 100),
                capacity=self.rng.choice([2, 3, 4])
            )
        self.allocator = TaskAllocator(self)
        self.failures = FailureHandler(self)

    def log_event(self, message: str, kind: str = "system") -> None:
        self.log.append({"time": round(self.time, 1), "message": message, "kind": kind})

    def add_vehicle(self, x: float, y: float, battery: float = 100.0, capacity: int = 3,
                    vehicle_id: Optional[str] = None) -> Vehicle:
        vid = vehicle_id or f"V{len(self.vehicles) + 1}"
        v = Vehicle(vid, float(x), float(y), float(battery), capacity)
        self.vehicles[vid] = v
        v.route = [v.destination] if v.destination else []
        return v

    def add_task(self, pickup: Point, dropoff: Point, priority: str = "normal") -> Task:
        if priority not in PRIORITY_RANK:
            raise ValueError("priority must be 'emergency', 'high', 'normal' or 'low'")
        self._task_counter += 1
        t = Task(f"T{self._task_counter}", tuple(pickup), tuple(dropoff),
                 priority, created_at=self.time)
        self.tasks[t.id] = t
        self.log_event(f"Task {t.id} created ({priority} priority)", "task")
        if self.allocator.allocate(t) is None:
            t.no_bidder_logged = True
            self.log_event(f"Task {t.id} has no eligible bidder; waiting in pool", "auction")
        return t

    def spawn_random_task(self) -> Task:
        pt = lambda: (round(self.rng.uniform(10, self.grid_w - 10)),
                      round(self.rng.uniform(10, self.grid_h - 10)))
        prio = self.rng.choices(["high", "normal", "low"], weights=[2, 5, 3])[0]
        return self.add_task(pt(), pt(), prio)

    def fail_vehicle(self, vehicle_id: str, mode: str = "breakdown") -> bool:
        ok = self.failures.fail_vehicle(vehicle_id, mode)
        if ok:
            self.event_counts["failures"] += 1
            self.event_counts["reauctions"] = sum(
                1 for e in self.log if "re-auctioned" in e["message"])
        return ok

    def set_traffic(self, intersection_id: str, level: Optional[float] = None, announce: bool = True) -> dict:
        if intersection_id not in self.traffic:
            raise ValueError("unknown intersection")
        if level is None:
            level = self.rng.uniform(0.1, 0.95)
        level = max(0.0, min(1.0, float(level)))
        # More traffic => lower effective speed.
        factor = max(0.38, 1.0 - level * 0.62)
        label = "Heavy" if level >= 0.70 else "Moderate" if level >= 0.40 else "Light"
        self.traffic[intersection_id] = {
            "level": round(level, 2), "label": label, "speed_factor": round(factor, 2)}
        if announce:
            self.log_event(
                f"Traffic update at {intersection_id}: {label} ({round(level * 100)}%)",
                "traffic")
        return self.get_state()

    def set_communication(self, vehicle_id: str, connected: bool = False,
                          duration: int = 6) -> dict:
        if vehicle_id not in self.vehicles:
            raise ValueError("unknown vehicle")
        v = self.vehicles[vehicle_id]
        if connected:
            v.connected, v.comm_loss_remaining = True, 0
            self.log_event(f"Communication RESTORED for {vehicle_id}; agent rejoined", "communication")
        else:
            v.connected, v.comm_loss_remaining = False, max(1, int(duration))
            self.event_counts["comm_losses"] += 1
            self.log_event(
                f"Communication LOST for {vehicle_id}; local autonomy continues",
                "communication")
        return self.get_state()

    def set_override(self, intersection_id: str, vehicle_id: Optional[str] = None) -> dict:
        if intersection_id not in self.traffic:
            raise ValueError("unknown intersection")
        if vehicle_id is None:
            self.emergency_override.pop(intersection_id, None)
            self.log_event(f"Human override CLEARED at {intersection_id}", "override")
        else:
            if vehicle_id not in self.vehicles:
                raise ValueError("unknown vehicle")
            self.emergency_override[intersection_id] = vehicle_id
            self.event_counts["overrides"] += 1
            self.log_event(
                f"Human override ACTIVE at {intersection_id}: {vehicle_id} receives GO priority",
                "override")
        return self.get_state()

    def _traffic_factor(self, v: Vehicle, nxt: Point) -> float:
        # Use the strongest congestion level near the planned segment.
        factors = []
        for iid, info in self.traffic.items():
            inter = next((i for i in self.intersections.intersections if i.id == iid), None)
            if inter and segment_point_distance(inter.center, v.pos, nxt) <= inter.radius + 32:
                factors.append(info["speed_factor"])
        return min(factors) if factors else 1.0

    def _run_demo_events(self) -> None:
        """Deterministic live-demo choreography; manual controls remain available."""
        if not self.demo_mode:
            return
        events = {
            4: ("failure", lambda: self.fail_vehicle("V3", "breakdown")),
            6: ("task", lambda: self.add_task((60, 200), (240, 60), "high")),
            9: ("comm", lambda: self.set_communication("V4", False, 6)),
            12: ("override", lambda: self.set_override("I2", "V2")),
            15: ("traffic", lambda: self.set_traffic("I2", 0.90)),
            18: ("clear", lambda: self.set_override("I2", None)),
        }
        key = int(round(self.time))
        if key in events and events[key][0] not in self._demo_fired:
            name, action = events[key]
            self._demo_fired.add(name)
            action()

    def tick(self) -> dict:
        self.time += self.dt
        self._run_demo_events()

        # Recover communication links automatically.
        for v in self.vehicles.values():
            if not v.connected and v.comm_loss_remaining > 0:
                v.comm_loss_remaining -= 1
                if v.comm_loss_remaining <= 0:
                    v.connected = True
                    self.log_event(
                        f"Communication RESTORED for {v.id}; agent rejoined the auction pool",
                        "communication")

        # Dynamic traffic drifts slightly so the city is genuinely changing.
        for iid, info in list(self.traffic.items()):
            if self.rng.random() < 0.18:
                self.set_traffic(iid, max(0.05, min(0.95, info["level"] + self.rng.uniform(-0.10, 0.10))), announce=False)

        if self.task_spawn_prob and self.rng.random() < self.task_spawn_prob:
            self.spawn_random_task()
        self.allocator.allocate_pending()

        plans: Dict[str, Point] = {}
        requests: List[Tuple[Vehicle, Intersection, float]] = []
        for v in self.vehicles.values():
            if v.status == "failed" or v.destination is None:
                continue
            nxt = self._next_position(v)
            plans[v.id] = nxt
            for inter in self.intersections.intersections:
                if inter.contains(v.x, v.y):
                    continue
                if segment_point_distance(inter.center, v.pos, nxt) <= inter.radius:
                    edge = max(0.0, dist(v.pos, inter.center) - inter.radius)
                    eta = (self.time - self.dt) + (edge / max(self.speed, 0.1)) * self.dt
                    requests.append((v, inter, eta))

        yielding = self.intersections.resolve(requests, self.vehicles.values(), self.time)
        self.event_counts["yields"] = sum(
            1 for e in self.log if " YIELD " in f" {e['message']} ")
        self.event_counts["auctions"] = sum(
            1 for e in self.log if "won Task" in e["message"] or "re-auctioned" in e["message"])
        self.event_counts["reauctions"] = sum(
            1 for e in self.log if "re-auctioned" in e["message"])

        for vid, nxt in plans.items():
            v = self.vehicles[vid]
            if vid in yielding:
                v.status = "negotiating"
                v.traffic_delay += 1
                continue
            factor = self._traffic_factor(v, nxt)
            move_speed = self.speed * factor
            target = v.route[v.route_index] if v.route and v.route_index < len(v.route) else v.destination
            d = dist(v.pos, target)
            if d <= move_speed:
                actual = target
            else:
                f = move_speed / d
                actual = (v.x + (target[0] - v.x) * f,
                          v.y + (target[1] - v.y) * f)
            moved = dist(v.pos, actual)
            v.battery = max(0.0, v.battery - moved * DRAIN_PER_UNIT)
            v.x, v.y = actual
            v.traffic_delay = max(0.0, v.traffic_delay - 0.25)
            v.status = "delivering"
            if v.route and dist(v.pos, v.route[v.route_index]) <= 1e-6:
                if v.route_index < len(v.route) - 1:
                    v.route_index += 1
                else:
                    self._handle_arrivals(v)

        for v in self.vehicles.values():
            if v.status == "idle":
                v.battery = min(100.0, v.battery + IDLE_RECHARGE)
        for v in list(self.vehicles.values()):
            self.failures.check_battery(v)
        return self.get_state()

    def _nearest_road(self, p: Point) -> Point:
        roads = [100.0, 200.0]
        candidates = []
        for x in roads:
            candidates.append((abs(p[0]-x), (x, p[1])))
        for y in roads:
            candidates.append((abs(p[1]-y), (p[0], y)))
        return min(candidates, key=lambda z: (z[0], z[1][0]+z[1][1]))[1]

    def build_route(self, start: Point, end: Point) -> List[Point]:
        """Build a simple road-constrained route on the 100/200 grid."""
        if dist(start, end) <= 1e-6:
            return [end]
        sx, sy = self._nearest_road(start)
        ex, ey = self._nearest_road(end)
        points: List[Point] = []
        if dist(start, (sx, sy)) > 1e-6:
            points.append((sx, sy))
        # Travel through an intersection when changing road axes.
        if abs(sx-ex) > 1e-6 and abs(sy-ey) > 1e-6:
            points.append((ex, sy))
        if dist((ex, sy), (ex, ey)) > 1e-6:
            points.append((ex, ey))
        if dist((sx, sy), (ex, sy)) > 1e-6 and not points:
            points.append((ex, sy))
        if dist(end, (ex, ey)) > 1e-6:
            points.append(end)
        elif not points or dist(points[-1], end) > 1e-6:
            points.append(end)
        # Remove consecutive duplicates.
        out=[]
        for q in points:
            if not out or dist(out[-1], q) > 1e-6:
                out.append(q)
        return out or [end]

    def _set_route(self, v: Vehicle, target: Point) -> None:
        v.destination = target
        v.route = self.build_route(v.pos, target)
        v.route_index = 0

    def _next_position(self, v: Vehicle) -> Point:
        if not v.route or v.route_index >= len(v.route):
            v.route = self.build_route(v.pos, v.destination) if v.destination else []
            v.route_index = 0
        target = v.route[v.route_index] if v.route else v.destination
        d = dist(v.pos, target)
        if d <= self.speed:
            return target
        f = self.speed / d
        return (v.x + (target[0] - v.x) * f,
                v.y + (target[1] - v.y) * f)

    def _handle_arrivals(self, v: Vehicle) -> None:
        for _ in range(3):
            if v.destination is None or dist(v.pos, v.destination) > 1e-6:
                return
            t = self.tasks[v.current_task]
            if v.phase == "to_pickup":
                t.picked_up, v.phase = True, "to_dropoff"
                self._set_route(v, t.dropoff)
                self.log_event(f"Vehicle {v.id} picked up Task {t.id}", "task")
            else:
                t.status, t.picked_up = "completed", False
                self.completed_count += 1
                self.log_event(f"Vehicle {v.id} completed Task {t.id}", "done")
                v.current_task, v.phase, v.destination = None, None, None
                v.route, v.route_index = [], 0
                v.start_next_task(self.tasks)
                if v.current_task:
                    self._set_route(v, v.destination)
                if v.current_task is None:
                    v.status = "idle"
                    return

    def get_state(self, log_limit: Optional[int] = None) -> dict:
        log = self.log if log_limit is None else self.log[-log_limit:]
        return {
            "time": round(self.time, 1),
            "grid": {"width": self.grid_w, "height": self.grid_h},
            "vehicles": [
                {"id": v.id, "x": round(v.x, 1), "y": round(v.y, 1),
                 "battery": round(v.battery), "capacity": v.capacity,
                 "status": v.status, "current_task": v.current_task,
                 "destination": list(v.destination) if v.destination else None,
                 "connected": v.connected,
                 "comm_loss_remaining": v.comm_loss_remaining,
                 "failure_reason": v.failure_reason,
                 "traffic_delay": round(v.traffic_delay, 1),
                 "route": [list(p) for p in v.route[v.route_index:]]}
                for v in self.vehicles.values()],
            "tasks": [
                {"id": t.id, "pickup": list(t.pickup), "dropoff": list(t.dropoff),
                 "priority": t.priority, "status": t.status, "assigned_to": t.assigned_to,
                 "picked_up": t.picked_up}
                for t in self.tasks.values()],
            "intersections": [
                {"id": i.id, "x": i.x, "y": i.y, "radius": i.radius,
                 "traffic": self.traffic.get(i.id, {}),
                 "override_vehicle": self.emergency_override.get(i.id)}
                for i in self.intersections.intersections],
            "traffic": self.traffic,
            "emergency_overrides": dict(self.emergency_override),
            "metrics": dict(self.event_counts),
            "log": [dict(e) for e in log],
            "completed_count": self.completed_count,
        }


if __name__ == "__main__":
    import sys
    full_json = "--json" in sys.argv
    n_ticks = int(sys.argv[sys.argv.index("--ticks") + 1]) if "--ticks" in sys.argv else 45
    from run_demo import scripted_demo
    sim = scripted_demo()
    seen_log = 0
    for step in range(1, n_ticks + 1):
        if step == 4:
            sim.fail_vehicle("V3", "breakdown")
        if step == 6:
            sim.add_task((60, 200), (240, 60), "high")
        if full_json:
            print(json.dumps(sim.tick()))
        else:
            state = sim.tick()
            print(f"--- t={sim.time:.1f} completed={state['completed_count']}")
            for e in state["log"][seen_log:]:
                print(f"  [log {e['time']:>5}] {e['message']}")
            seen_log = len(state["log"])
