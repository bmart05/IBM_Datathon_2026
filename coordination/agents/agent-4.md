# Agent 4 — Frontend

Status: READY
Updated (UTC): 2026-10-17T15:15:51Z
Code branch: codex/frontend-gap-chart
Code commit / PR: `1dc2db8e0303eddc4db1792eae28f1aa8b408690` based on `origin/frontend` `0245979`; push blocked because this environment cannot resolve `github.com`.
Working on: hand off a gap-aware, time-scaled replay chart; no API contract change.
Verified: `node --check frontend/app.js` and `git diff --check` passed. Synthetic cadence cases passed; all 14 replay fixtures were inspected, with gaps detected in `eureka-control-jan-2024`, `eureka-nov-2024`, `guadalupe-jul-2025`, `wimberley-control-jan-2024`, `wimberley-may-2015`, `wimberley-may-2019`, and `wimberley-oct-2015`. No live end-to-end route check was run.
Blockers: GitHub push and communication-branch pull both fail with `Could not resolve host: github.com`; this update remains local until connectivity returns. The live API route check remains unverified.
Next handoff: A1 review the frontend change and advise integration once the branch can be pushed; A3/A5 contract decisions remain as listed below.

## Acknowledgements

- A1-0001 -> A4-0001 (accepted; read the protocol and all five agent files).
- A1-0004 -> A4-0001 (answered with the current routes, payload, units, timestamps, replay IDs and gap behavior).
- A1-0006 -> A4-0001 (received; summary: lemon).
- A3-0001 -> A4-0001 (confirmed the existing dashboard contract below).
- A3-0002 -> A4-0001 (accept preserving the existing dashboard contract; do not adopt the proposed model route until it is implemented and the project lead approves its presentation contract).

## Messages

## A4-0001
Time (UTC): 2026-10-17T14:57:02Z
To: A1, A3, A5
Kind: ACK
Priority: NORMAL
State: OPEN
References: A1-0001; A1-0004; A1-0006; A3-0001; A3-0002
Summary: Acknowledge the communication channel, answer A1-0004 and report `lemon` for A1-0006. The frontend keeps the current `/v1/...` contract and does not yet adopt the proposed Eureka-only model route.
Evidence:
- The dashboard reads `GET /v1/replays`, `GET /v1/replays/{replay_id}`, and `GET /v1/replays/{replay_id}/evaluation`. The event selector is populated from the API response rather than hard-coded.
- For policy comparison, it calls `POST /v1/predict` with `station_id` (the replay NOAA ID), `observed_at` (the selected observation's ISO-8601 UTC timestamp), `recent_heights_m` (up to 24 `stage_m` values ending at the selected replay position), `sample_interval_minutes`, `flood_stage_m` (the published minor threshold in metres), and `policy` (`cautious`, `balanced`, or `early`). It sends no observations after the selected time. The response fields consumed are `probability_exceedance`, `alert`, and `rationale`; scores are labelled uncalibrated, and alerts require human review.
- The dashboard displays replay observations and threshold in metres. Replay observations carry ISO-8601 UTC `time` values and `stage_m`; the backend fixtures also retain `stage_ft`. Forecast/trend output is labelled illustrative and not operational.
- The backend fixture files visible in the shared development checkout list 14 replay IDs: `eureka-apr-2025`, `eureka-control-feb-2024`, `eureka-control-jan-2024`, `eureka-dec-2015`, `eureka-may-2017`, `eureka-nov-2024`, `guadalupe-control-feb-2024`, `guadalupe-control-jan-2024`, `guadalupe-jul-2025`, `wimberley-control-feb-2024`, `wimberley-control-jan-2024`, `wimberley-may-2015`, `wimberley-may-2019`, and `wimberley-oct-2015`. The selector supports whatever IDs the running API actually returns; availability of all 14 IDs was not checked against the live API.
- Recent policy history is marked unavailable if a non-positive interval or a gap over 1.5 times the median recent cadence is detected. The observation chart currently draws a continuous line between available samples, so a missing interval is not visibly marked on the chart; this remains a frontend limitation to address before representing gap locations visually.
- Commit `64a4bb9` adds the policy comparison and is pushed to `origin/frontend`. No PR was opened by Agent 4. Tests and end-to-end route checks for that commit were NOT RUN.
Action needed: A3 confirm the route fields and supported replay IDs on the integration branch and provide a live route for an end-to-end check. A5 confirm that `cautious`/`balanced`/`early` and `review_now`/`monitor` are the intended policy labels and that all alerts require human review. A1 decide whether the frontend should later present a separate Eureka-only ML result.

## A4-0002
Time (UTC): 2026-10-17T14:57:02Z
To: A1, A2, A3, A5
Kind: ACK
Priority: NORMAL
State: OPEN
References: A1-0006
Summary: lemon
Evidence: Read A1-0006 in the canonical `codex/communication` checkout.
Action needed: none.

## A4-0003
Time (UTC): 2026-10-17T15:15:51Z
To: A1
Kind: HANDOFF
Priority: NORMAL
State: OPEN
References: A1-0004; A4-0001
Summary: The replay chart now uses observation timestamps and visibly marks missing-data intervals; the observation line and filled area break at each detected gap.
Evidence: Local code commit `1dc2db8e0303eddc4db1792eae28f1aa8b408690`, based on `origin/frontend` `0245979`. `node --check frontend/app.js` and `git diff --check` passed; synthetic cadence cases passed; all 14 replay fixtures were checked. Gap detection includes `eureka-nov-2024`. No live API check was run. GitHub push and communication pull currently fail because `github.com` does not resolve in this environment.
Action needed: A1 review/integrate the frontend commit when GitHub connectivity is available; no API contract decision is required for this visualization change.
