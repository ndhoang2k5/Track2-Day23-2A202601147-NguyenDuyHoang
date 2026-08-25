"""Ordered restore, readiness validation, and regional cutover."""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    record = {"ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)), **kw}
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return record


def state_of(region: str, timeout: float = 2.0) -> dict:
    response = httpx.get(f"{URL[region]}/v1/state", timeout=timeout)
    response.raise_for_status()
    return response.json()


def failover(target: str, backend: str, wait: float) -> dict:
    """Run all five steps; never cut over when readiness does not pass."""
    if target not in URL or backend not in {"fs", "minio"} or wait < 0:
        raise ValueError("invalid target/backend/wait")
    primary = "b" if target == "a" else "a"
    try:
        before = state_of(target)
    except Exception as exc:
        before = {"region": target, "error": type(exc).__name__}
    emit(step="1_verify_target", target=target, state=before)
    try:
        restored = snapshot.get(target, backend)
        rpo = snapshot.rpo(pathlib.Path(f"state/region-{primary}/vectors.sqlite"),
                           pathlib.Path(f"state/region-{target}/vectors.sqlite"))
    except (Exception, SystemExit) as exc:
        emit(step="2_restore_snapshot", target=target, ok=False,
             error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "target": target, "failed_step": "2_restore_snapshot",
                "error": str(exc), "state": before}
    restore_event = emit(
        step="2_restore_snapshot", target=target, ok=True,
        rpo_seconds=rpo["rpo_seconds"], docs_lost=rpo["docs_lost"],
        embed_model_version=restored.get("embed_model_version"),
        snapshot_at=restored.get("snapshot_at"),
    )
    pool_file = pathlib.Path(f"state/region-{target}/pool_state")
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text("full\n", encoding="utf-8")
    emit(step="3_scale_pool", target=target, pool_state="full")

    started = time.monotonic()
    deadline = started + wait
    ready_body = None
    last_error = "readiness timeout"
    while True:
        try:
            response = httpx.get(f"{URL[target]}/readyz", timeout=min(2.0, max(0.1, wait)))
            ready_body = response.json()
            if response.status_code == 200 and ready_body.get("ready", True):
                break
            last_error = ",".join(ready_body.get("reasons", [])) or f"http_{response.status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            emit(step="4_wait_ready", target=target, ok=False,
                 waited_s=round(time.monotonic() - started, 2), error=last_error)
            return {"ok": False, "target": target, "failed_step": "4_wait_ready",
                    "error": last_error, "state": before, "restore": restore_event}
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    waited = round(time.monotonic() - started, 2)
    emit(step="4_wait_ready", target=target, ok=True, waited_s=waited, state=ready_body)
    active = pathlib.Path("edge/active_region")
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(target, encoding="utf-8")
    emit(step="5_dns_cutover", target=target, ok=True, active_region=target)
    final_state = state_of(target)
    return {"ok": True, "target": target, "active_region": target, "state": final_state,
            "restore": restore_event, "waited_s": waited}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="b", choices=["a", "b"])
    parser.add_argument("--backend", default="fs", choices=["fs", "minio"])
    parser.add_argument("--wait", type=float, default=60)
    args = parser.parse_args()
    print(json.dumps(failover(args.target, args.backend, args.wait), indent=2))
