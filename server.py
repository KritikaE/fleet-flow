"""Dashboard server: runs Person A's engine and exposes it over HTTP. Standard library only.
   python server.py  ->  open http://localhost:8000
"""
import json, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from fleet_sim import Simulation
from run_demo import scripted_demo

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css",
          "/app.js": "app.js", "/sample_states.jsonl": "sample_states.jsonl"}
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".jsonl": "text/plain"}
lock, sim = threading.Lock(), scripted_demo()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/state":
            with lock:
                return self._send(200, sim.get_state())
        if path in STATIC:
            f = os.path.join(HERE, STATIC[path])
            with open(f, "rb") as fh:
                return self._send(200, fh.read(), TYPES[os.path.splitext(f)[1]])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        global sim
        n = int(self.headers.get("Content-Length") or 0)
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid JSON"})
        route = self.path.split("?")[0]
        with lock:
            try:
                if route == "/api/tick":
                    return self._send(200, sim.tick())
                if route == "/api/reset":
                    sim = (Simulation(num_vehicles=6, task_spawn_prob=0.12, seed=1)
                           if b.get("random") else scripted_demo())
                    return self._send(200, sim.get_state())
                if route == "/api/task":
                    sim.add_task(tuple(b["pickup"]), tuple(b["dropoff"]),
                                 b.get("priority", "normal"))
                    return self._send(200, sim.get_state())
                if route == "/api/fail":
                    sim.fail_vehicle(b["id"], b.get("mode", "breakdown"))
                    return self._send(200, sim.get_state())
                if route == "/api/repair":
                    sim.failures.repair_vehicle(b["id"])
                    return self._send(200, sim.get_state())
                if route == "/api/communication":
                    return self._send(200, sim.set_communication(
                        b["id"], bool(b.get("connected", False)), int(b.get("duration", 6))))
                if route == "/api/traffic":
                    return self._send(200, sim.set_traffic(
                        b["intersection"], b.get("level")))
                if route == "/api/override":
                    return self._send(200, sim.set_override(
                        b["intersection"], b.get("vehicle")))
                return self._send(404, {"error": "unknown route"})
            except (KeyError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:
                return self._send(500, {"error": str(exc)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("Fleet dashboard on http://localhost:8000  (Ctrl+C to stop)")
    ThreadingHTTPServer(("", 8000), H).serve_forever()
