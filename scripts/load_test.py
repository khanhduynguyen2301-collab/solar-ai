"""Owner: Duy. Load-test the recommendation API and report latency percentiles and throughput.

Start the server with its rate limit off, then run this against it:

    $env:SOLAR_API_RATE_LIMIT_PER_MINUTE = "0"
    uvicorn src.api.app:app --port 8000
    python -m scripts.load_test

Each concurrency level sends the same number of POST /recommendations requests
(recorded mode, no LLM) over keep-alive connections and records how long every
one took. Results go to `data/evaluation/load_test.json`.

The numbers describe one server process on one machine with the load generator
running beside it, competing for the same CPU. They are not a capacity claim.
Standard library only.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import logging
import math
import os
import platform
import time
from pathlib import Path
from urllib.parse import urlsplit

from src.agents.trace import utc_now_iso
from src.common.config import DEFAULT_CONFIG
from src.models.data_loader import ROOT


LOGGER = logging.getLogger(__name__)
DEFAULT_OUTPUT = ROOT / "data" / "evaluation" / "load_test.json"
DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_REQUESTS = 2000
DEFAULT_CONCURRENCY = (1, 4, 16, 32)
WARMUP_REQUESTS = 50
PERCENTILES = (50, 90, 95, 99)
TIMEOUT_SECONDS = 30
# Requests cycle through the candidate angles, so the server is not answering one repeated question.
ANGLES = DEFAULT_CONFIG.candidate_angles_deg


def percentile(sorted_values: list[float], percent: float) -> float:
    """Nearest-rank percentile: the smallest value with at least `percent` of the samples at or below it."""
    if not sorted_values:
        raise ValueError("percentile of no samples")
    rank = max(1, math.ceil(percent / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize(latencies_s: list[float], statuses: list[int], elapsed_s: float, concurrency: int) -> dict:
    ordered = sorted(value * 1000 for value in latencies_s)
    ok = sum(1 for status in statuses if status == 200)
    return {
        "concurrency": concurrency,
        "requests": len(statuses),
        "successful": ok,
        "failed": len(statuses) - ok,
        "status_counts": {str(status): statuses.count(status) for status in sorted(set(statuses))},
        "duration_s": round(elapsed_s, 3),
        "throughput_rps": round(ok / elapsed_s, 1) if elapsed_s > 0 else None,
        "latency_ms": {
            **{f"p{percent}": round(percentile(ordered, percent), 2) for percent in PERCENTILES},
            "mean": round(sum(ordered) / len(ordered), 2), "min": round(ordered[0], 2), "max": round(ordered[-1], 2),
        },
    }


def _connect(url: str) -> http.client.HTTPConnection:
    parts = urlsplit(url)
    connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    return connection_type(parts.hostname, parts.port, timeout=TIMEOUT_SECONDS)


def _worker(url: str, count: int, offset: int) -> tuple[list[float], list[int]]:
    """One client on one keep-alive connection, sending `count` requests one after another."""
    connection, latencies, statuses = _connect(url), [], []
    try:
        for index in range(count):
            body = json.dumps({"current_angle_deg": ANGLES[(offset + index) % len(ANGLES)]})
            start = time.perf_counter()
            try:
                connection.request("POST", "/recommendations", body=body, headers={"content-type": "application/json"})
                response = connection.getresponse()
                response.read()
                status = response.status
            except (OSError, http.client.HTTPException):
                connection.close()
                connection, status = _connect(url), 0
            latencies.append(time.perf_counter() - start)
            statuses.append(status)
    finally:
        connection.close()
    return latencies, statuses


def run_level(url: str, total: int, concurrency: int) -> dict:
    per_worker = [total // concurrency + (1 if index < total % concurrency else 0) for index in range(concurrency)]
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda item: _worker(url, item[1], item[0]), enumerate(per_worker)))
    elapsed = time.perf_counter() - start
    return summarize([value for latencies, _ in results for value in latencies], [status for _, statuses in results for status in statuses], elapsed, concurrency)


def _get_json(url: str, path: str) -> dict:
    connection = _connect(url)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return {"status": response.status, **json.loads(response.read() or b"{}")}
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS, help="requests per concurrency level")
    parser.add_argument("--concurrency", type=int, nargs="+", default=list(DEFAULT_CONCURRENCY), help="numbers of simultaneous clients to test")
    parser.add_argument("--server-workers", type=int, default=1, help="how many server processes were started; recorded in the report, not checked")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        models = _get_json(arguments.url, "/models")
    except OSError as error:
        raise SystemExit(f"No server at {arguments.url}: {error}. Start it with: uvicorn src.api.app:app --port 8000")
    if models["status"] != 200:
        raise SystemExit(f"Server is not ready: {models}")
    LOGGER.info("Server: %s selected, trained on %s", models["selected_model"], models["dataset"]["name"])
    _worker(arguments.url, WARMUP_REQUESTS, 0)

    levels = []
    for concurrency in arguments.concurrency:
        level = run_level(arguments.url, arguments.requests, concurrency)
        levels.append(level)
        latency = level["latency_ms"]
        LOGGER.info(
            "concurrency %3d: %d ok, %d failed, %7.1f req/s, p50 %.1f ms, p95 %.1f ms, p99 %.1f ms, max %.1f ms",
            concurrency, level["successful"], level["failed"], level["throughput_rps"] or 0, latency["p50"], latency["p95"], latency["p99"], latency["max"],
        )
        if level["status_counts"].get("429"):
            LOGGER.info("  Some requests were rate limited: restart the server with SOLAR_API_RATE_LIMIT_PER_MINUTE=0")

    report = {
        "measured_at": utc_now_iso(),
        "note": "The server and the load generator ran on the same machine, sharing its CPU. Recorded mode, no LLM. Not a capacity claim.",
        "machine": {"system": platform.system(), "machine": platform.machine(), "logical_cpus": os.cpu_count(), "python": platform.python_version()},
        "server": {"url": arguments.url, "workers": arguments.server_workers, "selected_model": models["selected_model"], "dataset": models["dataset"], "trained_at": models["trained_at"]},
        "endpoint": "POST /recommendations",
        "percentile_method": "nearest rank",
        "warmup_requests": WARMUP_REQUESTS,
        "levels": levels,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Wrote %s", arguments.output)


if __name__ == "__main__":
    main()
