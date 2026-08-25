# RTO/RPO Evidence - Lab 23

All values below come from the real drill logs recorded on 2026-08-25 UTC.

## 1. Drill 1 - no DR

| Metric | Value | Method | Evidence |
|---|---:|---|---|
| t_outage | 2026-08-25T11:11:55Z | Region A chaos event | `chaos/chaos-events.jsonl:1` |
| First failed request | +0.2s | First `ok:false` after outage | `reports/drill-1-nodr.jsonl:31` |
| Later successful request | None | Every request through the end still failed | `reports/drill-1-nodr.jsonl:42` |
| RTO | NO_RECOVERY | No `ok:true` followed the first failure | `reports/drill-1-nodr.jsonl:42` |

## 2. Drill 2 - with DR

| Milestone | Seconds from t_outage | Method | Evidence |
|---|---:|---|---|
| t_outage | 0.0s | Region A chaos event | `chaos/chaos-events.jsonl:2` |
| First user-visible error | 0.1s | First `ok:false` | `reports/drill-2-withdr.jsonl:62` |
| Health checker detection | 15.8s | A became `UNHEALTHY` after 3 consecutive failures | `reports/health-events.jsonl:2` |
| Snapshot restored | 16.3s | `2_restore_snapshot` completed | `reports/failover-events.jsonl:2` |
| Region B ready | 22.8s | `4_wait_ready` returned ready | `reports/failover-events.jsonl:4` |
| DNS cutover | 22.8s | `5_dns_cutover` ran after readiness | `reports/failover-events.jsonl:5` |
| **First recovered request** | **26.1s** | HTTP 200 with `served_by:b` | `reports/drill-2-withdr.jsonl:74` |

| Metric | Measured | Target | Verdict |
|---|---:|---:|---|
| RTO - Inference API | 26.1s | 300s | PASS |
| RPO - Vector DB | 289.41s / 32 documents | 300s | PASS |

The RPO and lost-document count were computed at restore time in `reports/failover-events.jsonl:2`.

## 3. RTO breakdown

| Component | Seconds | Source | Reduction option |
|---|---:|---|---|
| Health-check detection | 15.8s (configured floor 15.0s) | `interval_s:5.0 * threshold:3` in `reports/health-events.jsonl:2` | Shorter interval while retaining anti-flap threshold |
| Restore after detection | 0.4s | Detection to restore event in `reports/failover-events.jsonl:2` | Incremental restore or hot replica |
| GPU pool warm-up | 6.5s | `waited_s:6.47` in `reports/failover-events.jsonl:4` | Keep warm capacity and preload weights |
| DNS/LB TTL and request phase | 3.3s | Recovery minus cutover using `reports/drill-2-withdr.jsonl:74` and `reports/failover-events.jsonl:5` | Lower TTL or active global LB |
| **Total (0.1s rounding)** | **26.0s, approximately RTO 26.1s** | Timestamped events above | - |
