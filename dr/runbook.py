"""Seven-step semi-automated primary-region outage runbook."""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr.health_checker import probe  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    record = {"ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)),
              "step": n, "name": name, **kw}
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return record


def confirm(auto: bool, msg: str) -> bool:
    return True if auto else input(f"{msg} [y/N] ").strip().lower() == "y"


def _latest_outage(primary: str):
    path = pathlib.Path("chaos/chaos-events.jsonl")
    found = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("action") == "kill" and event.get("region") == primary:
                found = event
    return found


def _wait_for_detection(primary: str, outage_ts: float, timeout: float = 30) -> bool:
    path = pathlib.Path("reports/health-events.jsonl")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if any(e.get("region") == primary and e.get("to") == "UNHEALTHY"
                   and e.get("ts", 0) >= outage_ts for e in events):
                return True
        time.sleep(0.25)
    return False


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    if primary == target or primary not in URL or target not in URL:
        raise ValueError("primary and target must be distinct valid regions")
    started = time.time()
    checks = []
    for attempt in range(3):
        ready, reason = probe(primary, timeout=2.0)
        checks.append({"ready": ready, "reason": reason})
        if attempt < 2:
            time.sleep(1.0)
    try:
        target_alive = httpx.get(f"{URL[target]}/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        target_alive = False
    outage_confirmed = all(not item["ready"] for item in checks) and target_alive
    step(1, "xac_nhan_outage", primary=primary, target=target,
         outage_confirmed=outage_confirmed, probes=checks, target_alive=target_alive)
    if not outage_confirmed:
        return {"ok": False, "failed_step": 1, "reason": "outage_not_confirmed"}
    if not confirm(auto, f"Region {primary} unavailable; fail over to {target}?"):
        step(2, "thong_bao_incident", confirmed=False, aborted=True)
        return {"ok": False, "failed_step": 2, "reason": "operator_declined"}

    outage = _latest_outage(primary)
    incident = step(2, "thong_bao_incident", confirmed=True,
                    outage_ts=None if outage is None else outage.get("ts"),
                    outage_iso=None if outage is None else outage.get("iso"))
    if outage is not None and not _wait_for_detection(primary, outage["ts"]):
        step(3, "scale_gpu_pool", failover_ok=False, failed_step="health_detection_timeout")
        return {"ok": False, "failed_step": 3, "reason": "health_detection_timeout"}

    result = fo.failover(target, backend, wait=60)
    step(3, "scale_gpu_pool", failover_ok=result.get("ok"), failed_step=result.get("failed_step"))
    if not result.get("ok"):
        return {"ok": False, "failed_step": 3, "failover": result}
    state = result.get("state", {})
    state_ok = bool(state.get("weights") and state.get("count", 0) > 0)
    step(4, "verify_state_replica", ok=state_ok, vector_count=state.get("count"),
         weights=state.get("weights"))
    cutover_ok = result.get("active_region") == target
    step(5, "dns_cutover", ok=cutover_ok, active_region=result.get("active_region"))

    latencies, errors = [], 0
    for _ in range(10):
        t0 = time.monotonic()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", timeout=3)
            if response.status_code != 200 or response.json().get("error"):
                errors += 1
        except httpx.HTTPError:
            errors += 1
        latencies.append((time.monotonic() - t0) * 1000)
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    step(6, "verify_golden_signals", requests=10, errors=errors,
         error_rate=errors / 10, p95_latency_ms=round(p95, 2))
    elapsed = round(time.time() - started, 2)
    step(7, "post_incident", elapsed_s=elapsed,
         measure_command="python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return {"ok": state_ok and cutover_ok and errors == 0, "target": target,
            "elapsed_s": elapsed, "p95_latency_ms": round(p95, 2), "error_rate": errors / 10,
            "incident": incident, "failover": result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", default="a")
    parser.add_argument("--target", default="b")
    parser.add_argument("--backend", default="fs", choices=["fs", "minio"])
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.primary, args.target, args.backend, args.auto), indent=2))
