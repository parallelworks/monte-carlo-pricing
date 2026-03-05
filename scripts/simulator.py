#!/usr/bin/env python3
"""Monte Carlo option pricing engine — simulates GBM paths and prices exotic options."""

import argparse
import json
import math
import sys
import time

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    import random
    HAS_NUMPY = False


def generate_paths_numpy(S0, r, sigma, T, n_paths, n_steps, seed):
    """Generate GBM price paths using numpy."""
    rng = np.random.default_rng(seed)
    dt = T / n_steps
    Z = rng.standard_normal((n_paths, n_steps))
    drift = (r - 0.5 * sigma**2) * dt
    diffusion = sigma * math.sqrt(dt)
    log_returns = drift + diffusion * Z
    log_paths = np.cumsum(log_returns, axis=1)
    # Prepend S0
    paths = S0 * np.exp(np.hstack([np.zeros((n_paths, 1)), log_paths]))
    return paths  # shape: (n_paths, n_steps + 1)


def generate_paths_pure(S0, r, sigma, T, n_paths, n_steps, seed):
    """Generate GBM price paths using pure Python random."""
    random.seed(seed)
    dt = T / n_steps
    drift = (r - 0.5 * sigma**2) * dt
    diffusion = sigma * math.sqrt(dt)
    paths = []
    for _ in range(n_paths):
        path = [S0]
        for _ in range(n_steps):
            Z = random.gauss(0, 1)
            path.append(path[-1] * math.exp(drift + diffusion * Z))
        paths.append(path)
    return paths


def price_option(paths, option_type, K, barrier=None):
    """Compute discounted payoffs for each path.

    Args:
        paths: Price paths — numpy array (n_paths, n_steps+1) or list of lists
        option_type: One of asian_call, asian_put, european_call, european_put, barrier_up_and_out_call
        K: Strike price
        barrier: Barrier level (for barrier options)

    Returns:
        List/array of payoffs (undiscounted)
    """
    if HAS_NUMPY and isinstance(paths, np.ndarray):
        return _price_numpy(paths, option_type, K, barrier)
    return _price_pure(paths, option_type, K, barrier)


def _price_numpy(paths, option_type, K, barrier):
    S_final = paths[:, -1]
    if option_type == "european_call":
        return np.maximum(S_final - K, 0.0)
    elif option_type == "european_put":
        return np.maximum(K - S_final, 0.0)
    elif option_type == "asian_call":
        S_avg = np.mean(paths[:, 1:], axis=1)  # exclude S0 from average
        return np.maximum(S_avg - K, 0.0)
    elif option_type == "asian_put":
        S_avg = np.mean(paths[:, 1:], axis=1)
        return np.maximum(K - S_avg, 0.0)
    elif option_type == "barrier_up_and_out_call":
        S_max = np.max(paths[:, 1:], axis=1)
        payoff = np.maximum(S_final - K, 0.0)
        payoff[S_max >= barrier] = 0.0
        return payoff
    else:
        raise ValueError(f"Unknown option type: {option_type}")


def _price_pure(paths, option_type, K, barrier):
    payoffs = []
    for path in paths:
        S_final = path[-1]
        monitoring = path[1:]  # exclude S0
        if option_type == "european_call":
            payoffs.append(max(S_final - K, 0.0))
        elif option_type == "european_put":
            payoffs.append(max(K - S_final, 0.0))
        elif option_type == "asian_call":
            S_avg = sum(monitoring) / len(monitoring)
            payoffs.append(max(S_avg - K, 0.0))
        elif option_type == "asian_put":
            S_avg = sum(monitoring) / len(monitoring)
            payoffs.append(max(K - S_avg, 0.0))
        elif option_type == "barrier_up_and_out_call":
            S_max = max(monitoring)
            p = max(S_final - K, 0.0)
            payoffs.append(0.0 if S_max >= barrier else p)
        else:
            raise ValueError(f"Unknown option type: {option_type}")
    return payoffs


def black_scholes_call(S0, K, r, sigma, T):
    """Black-Scholes closed-form for European call."""
    from math import log, sqrt, exp
    d1 = (log(S0 / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    # Standard normal CDF approximation
    N_d1 = _norm_cdf(d1)
    N_d2 = _norm_cdf(d2)
    return S0 * N_d1 - K * exp(-r * T) * N_d2


def black_scholes_put(S0, K, r, sigma, T):
    """Black-Scholes closed-form for European put."""
    from math import log, sqrt, exp
    d1 = (log(S0 / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    N_neg_d1 = _norm_cdf(-d1)
    N_neg_d2 = _norm_cdf(-d2)
    return K * exp(-r * T) * N_neg_d2 - S0 * N_neg_d1


def _norm_cdf(x):
    """Approximation of the standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def compute_histogram(payoffs, n_bins=50):
    """Compute histogram counts and edges."""
    if HAS_NUMPY and isinstance(payoffs, np.ndarray):
        counts, edges = np.histogram(payoffs, bins=n_bins)
        return counts.tolist(), edges.tolist()
    # Pure Python histogram
    payoffs_list = list(payoffs)
    if not payoffs_list:
        return [0] * n_bins, [0.0] * (n_bins + 1)
    min_val = min(payoffs_list)
    max_val = max(payoffs_list)
    if min_val == max_val:
        max_val = min_val + 1.0
    bin_width = (max_val - min_val) / n_bins
    edges = [min_val + i * bin_width for i in range(n_bins + 1)]
    counts = [0] * n_bins
    for v in payoffs_list:
        idx = int((v - min_val) / bin_width)
        if idx >= n_bins:
            idx = n_bins - 1
        counts[idx] += 1
    return counts, edges


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo Option Pricing Simulator")
    parser.add_argument("--batch-id", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--option-type", type=str, default="asian_call",
                        choices=["asian_call", "asian_put", "european_call", "european_put",
                                 "barrier_up_and_out_call"])
    parser.add_argument("--spot-price", type=float, default=100.0)
    parser.add_argument("--strike-price", type=float, default=100.0)
    parser.add_argument("--volatility", type=float, default=0.2)
    parser.add_argument("--risk-free-rate", type=float, default=0.05)
    parser.add_argument("--time-to-expiry", type=float, default=1.0)
    parser.add_argument("--barrier-level", type=float, default=150.0)
    parser.add_argument("--monitoring-points", type=int, default=252)
    parser.add_argument("--site-id", type=str, default="unknown")
    parser.add_argument("--cluster-name", type=str, default="")
    parser.add_argument("--scheduler-type", type=str, default="")
    parser.add_argument("--num-workers", type=int, default=1)
    args = parser.parse_args()

    seed = 42 + args.batch_id
    t0 = time.time()

    # Generate paths
    if HAS_NUMPY:
        paths = generate_paths_numpy(
            args.spot_price, args.risk_free_rate, args.volatility,
            args.time_to_expiry, args.batch_size, args.monitoring_points, seed
        )
    else:
        paths = generate_paths_pure(
            args.spot_price, args.risk_free_rate, args.volatility,
            args.time_to_expiry, args.batch_size, args.monitoring_points, seed
        )

    # Price option
    payoffs = price_option(paths, args.option_type, args.strike_price, args.barrier_level)

    # Discount factor
    discount = math.exp(-args.risk_free_rate * args.time_to_expiry)

    # Compute statistics on discounted payoffs
    if HAS_NUMPY and isinstance(payoffs, np.ndarray):
        discounted = payoffs * discount
        mean_payoff = float(np.mean(discounted))
        variance = float(np.var(discounted, ddof=1)) if len(discounted) > 1 else 0.0
        std_dev = float(np.std(discounted, ddof=1)) if len(discounted) > 1 else 0.0
        min_payoff = float(np.min(discounted))
        max_payoff = float(np.max(discounted))
        zero_count = int(np.sum(discounted == 0.0))
        hist_counts, hist_edges = compute_histogram(discounted, n_bins=50)
    else:
        discounted = [p * discount for p in payoffs]
        n = len(discounted)
        mean_payoff = sum(discounted) / n if n > 0 else 0.0
        if n > 1:
            variance = sum((x - mean_payoff) ** 2 for x in discounted) / (n - 1)
            std_dev = math.sqrt(variance)
        else:
            variance = 0.0
            std_dev = 0.0
        min_payoff = min(discounted) if discounted else 0.0
        max_payoff = max(discounted) if discounted else 0.0
        zero_count = sum(1 for x in discounted if x == 0.0)
        hist_counts, hist_edges = compute_histogram(discounted, n_bins=50)

    elapsed_ms = round((time.time() - t0) * 1000, 1)

    # Black-Scholes reference for European options
    bs_price = None
    if args.option_type == "european_call":
        bs_price = round(black_scholes_call(
            args.spot_price, args.strike_price, args.risk_free_rate,
            args.volatility, args.time_to_expiry), 6)
    elif args.option_type == "european_put":
        bs_price = round(black_scholes_put(
            args.spot_price, args.strike_price, args.risk_free_rate,
            args.volatility, args.time_to_expiry), 6)

    result = {
        "batch_id": args.batch_id,
        "batch_size": args.batch_size,
        "option_type": args.option_type,
        "mean_payoff": round(mean_payoff, 6),
        "variance": round(variance, 6),
        "std_dev": round(std_dev, 6),
        "min_payoff": round(min_payoff, 6),
        "max_payoff": round(max_payoff, 6),
        "zero_count": zero_count,
        "histogram_counts": hist_counts,
        "histogram_edges": [round(e, 6) for e in hist_edges],
        "simulation_time_ms": elapsed_ms,
        "site_id": args.site_id,
        "cluster_name": args.cluster_name,
        "scheduler_type": args.scheduler_type,
        "num_workers": args.num_workers,
    }
    if bs_price is not None:
        result["bs_price"] = bs_price

    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
