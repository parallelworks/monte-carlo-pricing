# Multi-Site Monte Carlo Option Pricing

A turnkey demo workflow for [Parallel Works ACTIVATE](https://www.parallel.works/) that prices exotic options using Monte Carlo simulation across two compute sites, with a live dashboard showing payoff histograms and price convergence in real-time.

![Thumbnail](thumbnail.png)

## What It Does

1. **Splits simulation** across two ACTIVATE resources (e.g., an on-prem GPU server + a cloud Slurm cluster)
2. **Runs Monte Carlo paths** in parallel — each site simulates half the total paths using multiprocessing
3. **Streams batch results** via HTTP POST to a live dashboard running on the on-prem resource
4. **Displays results** in real-time through the ACTIVATE session proxy — payoff histogram, price convergence chart, and per-site throughput

## Architecture

```
              ACTIVATE Workflow
                    │
         ┌──────────┴──────────┐
         ▼                      ▼
  Cloud Cluster (Slurm)   On-Prem (SSH)
  simulates paths N/2..N  simulates paths 0..N/2
         │                      │
         └── POST batches ──────┘
                    │
              Dashboard Server
              (on-prem:PORT)
                    │
            ACTIVATE Session Proxy
                    │
              User's Browser
```

## Quick Start

1. Start two ACTIVATE resources:
   - An **on-prem/existing** resource (e.g., `a30gpuserver`) — hosts the dashboard and runs half the simulation
   - A **cloud cluster** (e.g., `googlerockyv3`) — runs the other half of the simulation
2. Run the workflow from the ACTIVATE UI or CLI:
   ```bash
   pw workflows run monte_carlo_pricing
   ```
3. Open the session link to watch the price converge in real-time

## Inputs

| Input | Description | Default |
|-------|-------------|---------|
| On-Prem Resource | Always-on resource for dashboard + simulation | — |
| Cloud Resource | Cloud cluster for the other half of simulation | — |
| Option Type | Exotic option to price (Asian, European, Barrier) | Asian Call |
| Spot Price | Current underlying asset price | 100 |
| Strike Price | Option strike price | 100 |
| Volatility | Annualized volatility (sigma) | 0.2 |
| Risk-Free Rate | Annualized risk-free rate | 0.05 |
| Time to Expiry | Years until expiration | 1.0 |
| Barrier Level | Barrier level (barrier options only) | 150 |
| Monitoring Points | Time steps per path (252 = daily) | 252 |
| Total Simulations | Total MC paths across both sites | 500K |
| Batch Size | Paths per batch (controls update frequency) | 10K |
| Worker Threads | Parallel processes per site | Auto |

## Dashboard Features

- **Price convergence chart** — running MC estimate with narrowing confidence interval
- **Payoff histogram** — terminal payoff distribution across all simulated paths
- **Per-site throughput** — batch completion rate by site, color-coded
- **Live statistics** — elapsed time, paths/sec, current price estimate, standard error
- **Late-join support** — opening the dashboard mid-run shows all completed batches

## File Structure

```
├── workflow.yaml              # ACTIVATE workflow definition
├── thumbnail.png              # Workflow thumbnail
├── README.md                  # This file
└── scripts/
    ├── setup.sh               # Installs Python dependencies on remote hosts
    ├── start_dashboard.sh     # Launches FastAPI dashboard server
    ├── run_simulation.sh      # Runs MC simulation workers and POSTs batches
    ├── setup_tunnel.sh        # Reverse SSH tunnel for cross-site dashboard access
    ├── dashboard.py           # FastAPI + WebSocket live dashboard
    ├── simulator.py           # Monte Carlo option pricing engine
    └── templates/
        └── index.html         # Dashboard UI (charts + live stats)
```

## Requirements

- **Python 3.6+** on both compute resources
- **NumPy** (auto-installed for simulation)
- **FastAPI + Uvicorn + websockets** (auto-installed on the dashboard host)
- No GPU required — pure CPU simulation
