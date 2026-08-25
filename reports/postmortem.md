# Postmortem - DR Drill Lab 23

## 1. Timeline

| UTC time | Event | Evidence |
|---|---|---|
| 2026-08-25T11:13:42Z | Region A stopped serving | `chaos/chaos-events.jsonl:2` |
| 2026-08-25T11:13:42.218Z | First user received HTTP 503 | `reports/drill-2-withdr.jsonl:62` |
| 2026-08-25T11:13:51Z | Operator confirmed incident, 9.04s after outage | `reports/runbook-run.jsonl:2` |
| 2026-08-25T11:13:57.961Z | Health checker marked A unhealthy | `reports/health-events.jsonl:2` |
| 2026-08-25T11:14:04.872Z | B was ready and DNS cut over | `reports/failover-events.jsonl:5` |
| 2026-08-25T11:14:08.261Z | First successful request from B | `reports/drill-2-withdr.jsonl:74` |

## 2. RTO/RPO gap analysis

- RTO target: 300s; measured: 26.1s; positive budget gap: 273.9s.
- RPO target: 300s; measured: 289.41s and 32 documents lost; positive budget gap: only 10.59s.
- The largest RTO component was health detection: 15.8s, or 57.5% of user RTO.
- The riskiest gap is RPO. It passed but is close enough that one delayed replication could breach the objective.

## 3. Root cause - five whys

1. Users failed because the edge still routed to A after A stopped.
2. Routing remained on A until the checker confirmed failure and B became ready.
3. Detection took 15.8s because policy requires 3 consecutive probes at a 5s interval.
4. B needed another 6.47s because its active-passive compute pool was warm and state was restored during the incident.
5. RPO approached the limit because the snapshot's newest document was 289.41s behind the primary and no lag guardrail prevented this.

In a real outage, restore is the most fragile runbook step: the snapshot may be stale or lack a compatible embedding model version. The automation aborts before DNS cutover when restore or readiness fails, preventing a second outage on B.

## 4. Action items

| # | Action item | Owner | Deadline | Expected impact |
|---|---|---|---|---|
| 1 | Alert at snapshot lag above 120s and mark DR unready | Storage SRE | 2026-09-01 | Bound RPO at 120s instead of nearly 300s |
| 2 | Test 2s interval and threshold 3 over five game days | Reliability SRE | 2026-09-08 | Reduce detection floor by about 9s |
| 3 | Keep one B worker full and preloaded at peak hours | ML Platform | 2026-09-15 | Remove about 6.5s of warm-up |

## 5. Required questions

1. `interval * threshold = 5s * 3 = 15s`. Actual detection was 15.8s, 57.5% of the 26.1s RTO.
2. A 1s interval lowers the floor from 15s to 3s and could lower RTO about 12s to 14.1s. Probe load rises fivefold, and transient failures create more false-failover/flapping risk, so threshold and a circuit breaker remain necessary.
3. During a six-hour permanent primary loss, `docs_lost` is customer data accepted by primary but absent from restored state. Search and inference may use stale information; responders must identify affected customers and replay or reconcile from the source event log.
