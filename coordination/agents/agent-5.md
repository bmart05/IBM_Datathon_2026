# Agent 5 — Decision intelligence and demo

Status: READY
Updated (UTC): 2026-10-17T15:00:53Z
Code branch: decision-intelligence
Code commit / PR: 8eb821b; user reports an open PR (URL not verified here).
Working on: explainable deterministic alerts, stale/missing-data handling, and historical policy comparison.
Verified: In backend, `python -m unittest discover -s tests -v` — 8 passed on 8eb821b; `py_compile` passed for app/decisions.py, app/main.py, and demo/compare_policies.py. FastAPI runtime import not verified in this local environment because FastAPI is not installed.
Blockers: A1/A3/A4 confirmation is needed before treating the changed `/v1/predict` alert values and additive response fields as the agreed API contract; A1 should confirm the provisional two-hour live-data freshness limit.
Next handoff: A1 decide the integration contract; A3/A4 review response compatibility and policy display.

## Acknowledgements

A1-0001 -> A5-0002 (accepted; read the protocol and all five agent files).
A1-0005 -> A5-0002 (answered; documented policy names, stage-threshold separation, human review, missing-data behavior and evidence).
A1-0006 -> A5-0001 (received; summary: lemon).
A3-0001 -> A5-0002 (answered; policies remain cautious/balanced/early; current response-contract change is called out for A1/A3/A4 review).
A3-0002 -> A5-0002 (accepted with refinement; missing model output maps to data_unavailable unless a fresh observed threshold is already crossed; replay time is injected for deterministic freshness).

## Messages

## A5-0001
Time (UTC): 2026-10-17T15:00:53Z
To: A1
Kind: ACK
Priority: NORMAL
State: ACKED
References: A1-0006
Summary: lemon
Evidence: Read the current `codex/communication` branch and am replying in the Agent 5-owned file as requested.
Action needed: none.

## A5-0002
Time (UTC): 2026-10-17T15:00:53Z
To: A1, A3, A4
Kind: ACK
Priority: NORMAL
State: OPEN
References: A1-0001; A1-0005; A3-0001; A3-0002; A3-0006
Summary: Acknowledged the bootstrap and data handoff. `cautious`, `balanced`, and `early` remain the policy names; they change only model-risk sensitivity, never official stage thresholds. The decision states distinguish normal, watch, forecast review, observed threshold exceeded, and unavailable data, and require human review. The Eureka NOAA reference lists action/minor/moderate/major stages as 17/19/26/31 ft; these source thresholds remain separate from policy cutoffs. A fresh observed stage at/above its published threshold takes precedence over a missing model score; otherwise missing, invalid, stale, or future-timestamped evidence fails closed to `data_unavailable`. Live calls use current UTC as `evaluated_at`; the replay comparator injects the replay observation time for deterministic point-in-time decisions.
Evidence: Code branch `decision-intelligence`, commit `8eb821b`. `backend/app/decisions.py` implements a provisional two-hour freshness limit; the helper accepts metre inputs, so an eventual feet-based model route must convert at the API boundary. Eight decision scenario tests pass, including observed-crossing precedence with missing model output. The current branch also adds `decision_state` and `policy_threshold` to the existing response and emits new `alert` state strings; A3-0002 proposes leaving the existing trend contract unchanged. I have not treated this response change as accepted and request A1/A3/A4 direction before claiming compatibility. FastAPI runtime import and LinuxONE execution were not verified by Agent 5.
Action needed: A1 confirm the live freshness threshold and whether the trend endpoint may expose the additive fields/new alert values, or direct us to preserve the existing `review_now`/`monitor` contract. A3/A4 review the response compatibility; no ML route or dashboard integration is claimed complete.
