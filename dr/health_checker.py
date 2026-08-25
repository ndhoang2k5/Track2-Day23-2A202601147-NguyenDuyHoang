"""Readiness-based, anti-flap health checker for both regions."""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Return readiness and a compact diagnostic reason."""
    try:
        response = httpx.get(f"{URL[region]}/readyz", timeout=timeout)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and body.get("ready", True):
            return True, "ready"
        reasons = body.get("reasons")
        return False, ",".join(map(str, reasons)) if reasons else f"http_{response.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """Poll both regions and log only state transitions."""
    if interval <= 0 or timeout <= 0 or threshold < 1 or duration < 0:
        raise ValueError("interval/timeout must be positive, threshold >= 1, duration >= 0")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Healthy is the operational baseline; startup probes are not transitions.
    state = {region: "HEALTHY" for region in URL}
    failures = {region: 0 for region in URL}
    deadline = time.monotonic() + duration
    with out.open("a", encoding="utf-8") as log:
        while time.monotonic() < deadline:
            cycle_started = time.monotonic()
            for region in URL:
                ready, reason = probe(region, timeout)
                failures[region] = 0 if ready else failures[region] + 1
                desired = "HEALTHY" if ready else (
                    "UNHEALTHY" if failures[region] >= threshold else state[region]
                )
                if desired is not None and desired != state[region]:
                    record = {
                        "ts": time.time(), "region": region, "event": "state_change",
                        "from": state[region], "to": desired,
                        "reason": reason, "consecutive_fails": failures[region],
                        "interval_s": interval, "threshold": threshold,
                    }
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.flush()
                    state[region] = desired
            remaining = deadline - time.monotonic()
            sleep_for = min(max(0.0, interval - (time.monotonic() - cycle_started)), remaining)
            if sleep_for > 0:
                time.sleep(sleep_for)
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--threshold", type=int, default=3)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--out", default="reports/health-events.jsonl")
    args = parser.parse_args()
    run(args.interval, args.timeout, args.threshold, args.duration, pathlib.Path(args.out))
