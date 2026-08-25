# One-page Runbook - Primary Region Down

Scope: bare-mode lab, primary `a`, target `b`, snapshot backend `fs`. Run commands from the repository root. The automation is invoked exactly once in step 3; later steps only verify its output.

| # | Step | Copy-paste command | Completion signal | Owner |
|---|---|---|---|---|
| 1 | Confirm outage | `python chaos/kill_region.py status --backend bare` | Region A is not ready, Region B has `alive: true`, and A fails 3 consecutive `/readyz` probes. A single failure is not enough. | On-call SRE |
| 2 | Open incident and start RTO clock | `Get-Content chaos/chaos-events.jsonl -Tail 1` | Latest event is `action:kill` for A. Incident Commander records its `ts` as `t_outage` and announces the incident. | Incident Commander |
| 3 | Confirm and execute automated failover once | `python dr/runbook.py --primary a --target b --backend fs` | Operator enters `y`; command returns `ok:true`. It performs restore, scale, readiness wait, cutover, and golden checks exactly once. | Incident Commander |
| 4 | Verify restored state and pool readiness | `curl.exe -sf http://127.0.0.1:8002/readyz` | HTTP 200 with `ready:true`, vector count greater than zero, no warm-up reason, and `reports/failover-events.jsonl` contains successful steps 2 through 4. | Storage and ML Platform |
| 5 | Verify DNS/LB cutover | `curl.exe -s http://127.0.0.1:8080/edge/state` | After the TTL, response contains `active_region:b`; `5_dns_cutover` occurs after `4_wait_ready` in the failover log. | Network SRE |
| 6 | Verify golden signals | `1..10 | ForEach-Object { curl.exe -sf http://127.0.0.1:8002/v1/infer }` | 10 of 10 requests succeed from B. The runbook log records `error_rate:0` and measured p95 latency. | On-call SRE |
| 7 | Measure RTO/RPO and open postmortem | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | Output has `valid:true`, an empty warnings list, `rto_verdict:PASS`, and non-null RPO plus `docs_lost`. | Incident Commander |

## Abort conditions

Abort before DNS cutover if snapshot restore fails, embedding model version is missing or incompatible, vector count is zero, or Region B does not become ready within 60 seconds. The automation must leave `edge/active_region` unchanged. Escalate the failed step to the Incident Commander and ML Platform owner.

## Failback and rollback authority

- Failback is never automatic. Only the Incident Commander may approve returning traffic to A.
- Approval requires A to pass `/readyz` at least 3 consecutive times, state to be reconciled from B, model versions to match, and golden signals to remain stable for 15 minutes.
- After approval, run `python dr/failover.py --target a --backend fs --wait 60` once and repeat the 10 golden requests.
- If failback causes error rate above 1% or p95 latency above 500 ms, the Incident Commander orders rollback to B with `python dr/failover.py --target b --backend fs --wait 60`.
