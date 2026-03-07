#!/usr/bin/env python3
"""Live dashboard server — aggregates Monte Carlo batch results and streams to browser."""

import asyncio
import json
import math
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

TEMPLATE_DIR = Path(__file__).parent / "templates"

# In-memory state
state = {
    "config": None,          # Option parameters + simulation plan
    "total_paths": 0,        # Total paths completed
    "total_batches": 0,      # Total batches completed
    "planned_batches": 0,    # Expected total batches
    "planned_paths": 0,      # Expected total paths
    # Chan's parallel algorithm accumulators
    "running_sum": 0.0,      # Sum of all discounted payoffs (mean * count per batch)
    "running_m2": 0.0,       # Sum of squared deviations for online variance
    "running_count": 0,      # Total path count
    # Histogram (additive merge)
    "histogram_counts": None,  # List of counts per bin
    "histogram_edges": None,   # Bin edges (set from first batch)
    # Convergence trace
    "convergence": [],       # [{paths, mean, ci_low, ci_high, std_err}]
    # Site stats
    "site_stats": {},        # site_id -> {count, total_sim_ms, cluster_name, ...}
    "pending_sites": {},     # site_id -> {cluster_name, scheduler_type, timestamp}
    "start_time": None,
    "bs_price": None,        # Black-Scholes reference (for European options)
}
connected_ws: list[WebSocket] = []


def _reset_state():
    state["total_paths"] = 0
    state["total_batches"] = 0
    state["running_sum"] = 0.0
    state["running_m2"] = 0.0
    state["running_count"] = 0
    state["histogram_counts"] = None
    state["histogram_edges"] = None
    state["convergence"] = []
    state["site_stats"] = {}
    state["pending_sites"] = {}
    state["start_time"] = None
    state["bs_price"] = None


def _compute_throughput_history():
    """Build per-second throughput buckets from batch arrival times."""
    if not state["start_time"] or not state["site_stats"]:
        return []
    arrivals = []
    for site_id, stats in state["site_stats"].items():
        for ts in stats.get("arrival_times", []):
            arrivals.append((ts - state["start_time"], site_id))
    if not arrivals:
        return []
    arrivals.sort()
    max_t = arrivals[-1][0]
    buckets = []
    bucket_start = 0
    while bucket_start <= max_t:
        bucket_end = bucket_start + 1.0
        per_site = {}
        total = 0
        for rel_t, sid in arrivals:
            if bucket_start <= rel_t < bucket_end:
                per_site[sid] = per_site.get(sid, 0) + 1
                total += 1
        buckets.append({"ts_offset": round(bucket_start, 1), "total": total, "perSite": per_site})
        bucket_start += 1.0
    return buckets


@app.get("/", response_class=HTMLResponse)
async def index():
    return (TEMPLATE_DIR / "index.html").read_text()


@app.post("/api/config")
async def set_config(request: Request):
    """Set option parameters and simulation plan. Resets state."""
    body = await request.json()
    _reset_state()
    state["config"] = body
    state["planned_batches"] = body.get("total_batches", 0)
    state["planned_paths"] = body.get("total_simulations", 0)
    return {"status": "ok", "config": body}


@app.post("/api/worker/pending")
async def worker_pending(request: Request):
    """Register a site as pending (dispatched but not yet sending batches)."""
    data = await request.json()
    site_id = data.get("site_id", "unknown")
    cluster_name = data.get("cluster_name", "unknown")
    scheduler_type = data.get("scheduler_type", "ssh")
    state["pending_sites"][site_id] = {
        "cluster_name": cluster_name,
        "scheduler_type": scheduler_type,
        "timestamp": time.time(),
    }
    return {"status": "ok"}


@app.post("/api/batch")
async def receive_batch(request: Request):
    """Receive a batch result and update aggregation."""
    batch = await request.json()
    now = time.time()

    if state["start_time"] is None:
        state["start_time"] = now

    batch_size = batch["batch_size"]
    batch_mean = batch["mean_payoff"]
    batch_var = batch["variance"]
    site_id = batch.get("site_id", "unknown")

    # --- Chan's parallel merge for mean and variance ---
    n_a = state["running_count"]
    n_b = batch_size
    mean_a = state["running_sum"] / n_a if n_a > 0 else 0.0

    n_ab = n_a + n_b
    delta = batch_mean - mean_a
    state["running_sum"] += batch_mean * n_b
    state["running_m2"] += batch_var * (n_b - 1) + delta**2 * n_a * n_b / n_ab if n_ab > 0 else 0
    state["running_count"] = n_ab

    # Current price estimate
    current_mean = state["running_sum"] / n_ab if n_ab > 0 else 0.0
    current_var = state["running_m2"] / (n_ab - 1) if n_ab > 1 else 0.0
    std_err = math.sqrt(current_var / n_ab) if n_ab > 0 else 0.0
    ci_low = current_mean - 1.96 * std_err
    ci_high = current_mean + 1.96 * std_err

    state["total_paths"] += batch_size
    state["total_batches"] += 1

    # Merge histogram (additive)
    hist_counts = batch.get("histogram_counts")
    hist_edges = batch.get("histogram_edges")
    if hist_counts and hist_edges:
        if state["histogram_counts"] is None:
            state["histogram_counts"] = list(hist_counts)
            state["histogram_edges"] = list(hist_edges)
        else:
            # Additive merge — same bin edges assumed
            for i in range(min(len(state["histogram_counts"]), len(hist_counts))):
                state["histogram_counts"][i] += hist_counts[i]

    # Convergence point
    conv_point = {
        "paths": state["total_paths"],
        "mean": round(current_mean, 6),
        "ci_low": round(ci_low, 6),
        "ci_high": round(ci_high, 6),
        "std_err": round(std_err, 6),
    }
    state["convergence"].append(conv_point)

    # Store BS price if provided
    if "bs_price" in batch and state["bs_price"] is None:
        state["bs_price"] = batch["bs_price"]

    # Update site stats
    if site_id not in state["site_stats"]:
        state["site_stats"][site_id] = {
            "count": 0,
            "total_sim_ms": 0,
            "total_paths": 0,
            "first_ts": now,
            "cluster_name": batch.get("cluster_name", ""),
            "scheduler_type": batch.get("scheduler_type", ""),
            "num_workers": batch.get("num_workers", 1),
            "arrival_times": [],
        }
    stats = state["site_stats"][site_id]
    stats["count"] += 1
    stats["total_sim_ms"] += batch.get("simulation_time_ms", 0)
    stats["total_paths"] += batch_size
    stats["last_ts"] = now
    stats["arrival_times"].append(now)

    # Broadcast to all WebSocket clients
    msg = json.dumps({
        "type": "batch",
        "batch_id": batch.get("batch_id"),
        "site_id": site_id,
        "batch_size": batch_size,
        "simulation_time_ms": batch.get("simulation_time_ms", 0),
        "current_mean": round(current_mean, 6),
        "std_err": round(std_err, 6),
        "ci_low": round(ci_low, 6),
        "ci_high": round(ci_high, 6),
        "total_paths": state["total_paths"],
        "total_batches": state["total_batches"],
        "planned_batches": state["planned_batches"],
        "planned_paths": state["planned_paths"],
        "histogram_counts": state["histogram_counts"],
        "histogram_edges": state["histogram_edges"],
        "convergence": conv_point,
        "site_stats": {k: {kk: vv for kk, vv in v.items() if kk != "arrival_times"} for k, v in state["site_stats"].items()},
        "bs_price": state["bs_price"],
        "elapsed_s": round(now - state["start_time"], 1) if state["start_time"] else 0,
    })
    stale = []
    for ws in connected_ws:
        try:
            await ws.send_text(msg)
        except Exception:
            stale.append(ws)
    for ws in stale:
        connected_ws.remove(ws)

    return {"status": "ok", "total_batches": state["total_batches"]}


@app.get("/api/state")
async def get_state():
    """Return full state for late-joining browsers."""
    n = state["running_count"]
    current_mean = state["running_sum"] / n if n > 0 else 0.0
    current_var = state["running_m2"] / (n - 1) if n > 1 else 0.0
    std_err = math.sqrt(current_var / n) if n > 0 else 0.0

    return {
        "config": state["config"],
        "total_paths": state["total_paths"],
        "total_batches": state["total_batches"],
        "planned_batches": state["planned_batches"],
        "planned_paths": state["planned_paths"],
        "current_mean": round(current_mean, 6),
        "std_err": round(std_err, 6),
        "ci_low": round(current_mean - 1.96 * std_err, 6),
        "ci_high": round(current_mean + 1.96 * std_err, 6),
        "histogram_counts": state["histogram_counts"],
        "histogram_edges": state["histogram_edges"],
        "convergence": state["convergence"],
        "site_stats": {k: {kk: vv for kk, vv in v.items() if kk != "arrival_times"} for k, v in state["site_stats"].items()},
        "bs_price": state["bs_price"],
        "elapsed_s": round(time.time() - state["start_time"], 1) if state["start_time"] else 0,
        "throughput_history": _compute_throughput_history(),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_ws.append(ws)
    try:
        n = state["running_count"]
        current_mean = state["running_sum"] / n if n > 0 else 0.0
        await ws.send_text(json.dumps({
            "type": "init",
            "config": state["config"],
            "total_paths": state["total_paths"],
            "total_batches": state["total_batches"],
            "planned_batches": state["planned_batches"],
            "planned_paths": state["planned_paths"],
            "current_mean": round(current_mean, 6),
            "bs_price": state["bs_price"],
            "site_stats": {k: {kk: vv for kk, vv in v.items() if kk != "arrival_times"} for k, v in state["site_stats"].items()},
        }))
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        if ws in connected_ws:
            connected_ws.remove(ws)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DASHBOARD_PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
