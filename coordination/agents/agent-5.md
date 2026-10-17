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

## A5-0003
Time (UTC): 2026-10-17T15:14:49Z
To: A1, A3, A4
Kind: ACK + HANDOFF
Priority: URGENT
State: OPEN
References: A1-0006; A1-0007; A1-0011; A1-0012; A3-0011; coordination/TASKS.md
Summary: lemon. Accepted A1-0007 and A1-0011. Added a separate pure Eureka crossing-score decision helper while retaining the existing trend helper unchanged. The adapter signature is `decide_crossing_alert(*, policy, crossing_probability, current_stage_ft, flood_stage_ft, observed_at, as_of, data_quality_valid, sample_interval_minutes=15, fallback=None) -> AlertDecision`.
Evidence: Code branch `decision-intelligence`, head `3dbbc61` (includes `4b43363`). Adapter mapping: pass `crossing_probability` only (never `probability_exceedance`); pass measured feet and published 19.0-ft threshold; set `observed_at` to the latest measurement timestamp and `as_of` to replay time (live uses current UTC); `data_quality_valid` means the latest measured stage is fresh and source-approved, independently of feature-window completeness; pass the model's named fallback separately. Map `result.state.value` to `decision_state`, `result.policy_threshold` to `policy_threshold`, and `result.explanation` to `rationale`. A valid fresh observed stage >= threshold wins over a null model score or a feature-window fallback; invalid/stale/future measurement cannot support that claim. Cutoffs are inclusive at 0.75/0.55/0.35; otherwise a valid score is normal. No predicted peak is required. `backend` command `python -m unittest discover -s tests -v`: 15 passed; `python -m py_compile app/decisions.py tests/test_decisions.py ../demo/eureka_crossing_decisions.py`: passed. Demo: `python ../demo/eureka_crossing_decisions.py`; uses committed Eureka Nov 2024 below-threshold 2024-11-05T19:45Z with 25 six-hour rows, observed crossing 20:00Z, and an actual 30-minute gap in a 15-minute six-hour window at 2024-11-03T10:30Z. Demo policy score is illustrative and not model inference. It separately quotes the existing README holdout report (F1 0.729, AP 0.777; only two held-out Eureka floods, overlapping windows, not calibrated); Agent 2's new validation/evaluation may supersede those reported metrics.
Action needed: A3 consume the signature/mapping without changing `main.py` in this branch; A4 use state/rationale mapping. A2/A3 please confirm the selected replay timestamps fit the final validator/candidate, especially the 19:45Z below-threshold point. I will push the focused code branch and prepare its PR to main; no LinuxONE inference or new model evaluation is claimed.

## A5-0004
Time (UTC): 2026-10-17T15:18:00Z
To: A1
Kind: HANDOFF
Priority: URGENT
State: OPEN
References: A1-0011; A1-0012; A5-0003
Summary: Focused Agent 5 component is pushed and ready for your PR creation/review.
Evidence: Branch `codex/agent5-score-only`, exact tested head `e725316`, based on `origin/main`; contains only `backend/app/decisions.py`, `backend/tests/test_decisions.py`, `demo/compare_policies.py`, and `demo/eureka_crossing_decisions.py`. Push succeeded. PR creation link: https://github.com/bmart05/IBM_Datathon_2026/pull/new/codex/agent5-score-only. From `backend/`, `python -m unittest discover -s tests -v` passes 15 tests; `python -m py_compile app/decisions.py tests/test_decisions.py ../demo/compare_policies.py ../demo/eureka_crossing_decisions.py` passes; `python ../demo/eureka_crossing_decisions.py` reports the 25-row six-hour point, observed 19-ft crossing, actual 30-minute gap fallback, and distinctly labelled prior model report vs illustrative policy simulation. No LinuxONE inference is claimed. Main-based branch intentionally excludes Agent 3's `main.py` and API changes.
Action needed: Agent 1 create/review the PR to `main`; Agent 2/3 confirm replay point compatibility with their final validator/candidate. Do not use the prior 0.729 F1 as final if Agent 2's corrected evaluation changes it.
