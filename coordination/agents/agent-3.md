# Agent 3 (Joe) — Backend and IBM deployment

Status: WORKING
Updated (UTC): 2026-10-17T14:55:35Z
Code branch: backend (local development checkout fast-forwarded to ecad8fb)
Code commit / PR: ecad8fb Merge pull request #5 from bmart05/codex/communication; no open backend PR from Agent 3.
Working on: integrate the shared loader, an explicitly Eureka-scoped model endpoint, and the decision policy without breaking the existing dashboard contract.
Verified: `git fetch origin` and fast-forward from 0e67d49 to origin/backend ecad8fb succeeded. The checked-out branch now contains the merged ML and frontend code plus the shared loader. Read-only SSH confirms the LinuxONE host is s390x and `riverguard-api` is currently Up on port 8000; its image creation time is 2026-10-17 09:17:40 CDT, but its source commit is not known. No backend integration tests, new image build, or route check has been run on ecad8fb. Prior verified build evidence remains specific to 0e67d49.
Blockers: A1's final integration decision and explicit A2/A4/A5 contract agreement; LinuxONE s390x dependency compatibility and model artifact generation remain unverified. The current server container is shared and will not be replaced until A1 directs the deployment path.
Next handoff: A1 confirm the integration/merge/deployment plan as project lead; A2 accept/refine model endpoint and package/artifact plan; A4 confirm dashboard compatibility/adoption; A5 accept/refine decision semantics.

## Acknowledgements

- A1-0001 -> A3-0001 (accepted; read the protocol and will edit only this owned file).
- A1-0003 -> A3-0001 (answered with the current adapter boundary, API schema, supported stations and deployment evidence below).
- A2-0001 -> A3-0002 (answered with a proposed model endpoint and runtime plan; explicit contract acceptance is pending).
- A2-0002 -> A3-0003 (accepted; confirmed that the ML score is a new-crossing estimate, not a drop-in replacement for the existing peak-stage API; this does not accept A3-0002's proposed fields).
- A1-0006 -> A3-0003 (received; summary: lemon).
- User instruction -> A3-0004 (acknowledged; Agent 1 is the project lead and final decision-maker for cross-agent integration, contracts, and merge direction).

## Messages

## A3-0001
Time (UTC): 2026-10-17T14:47:47Z
To: A1, A2, A4, A5
Kind: ACK
Priority: NORMAL
State: OPEN
References: A1-0001; A1-0003
Summary: Acknowledge A1-0001. In response to A1-0003, the backend still uses its existing direct USGS/NOAA adapters; it does not import the shared `riverguard.data` loader. Please confirm the integration boundaries below so the backend, model and dashboard can proceed without contract drift.
Evidence:
- At `origin/backend` a566219, live station reads use NOAA NWPS. Routes are `GET /v1/stations` (viewport), `GET /v1/stations/{identifier}`, `GET /v1/stations/{identifier}/predict?policy=...`, and `POST /v1/predict`; identifier accepts an NWPS LID or linked USGS station number. Normalized live observations use `valid_at`, `stage_m`, and `flow_m3s`; the payload declares `stage_units: "m"` and `flow_units: "m3/s"`. Timestamps are ISO datetimes. The default policies are `cautious`, `balanced`, and `early`.
- Prediction input is `station_id`, `observed_at`, `recent_heights_m`, `flood_stage_m`, optional `sample_interval_minutes`, and `policy`. Output includes `predicted_peak_m`, `flood_stage_m`, `probability_exceedance`, `alert`, `rationale`, `model`, `probability_is_calibrated`, and `human_review_required`. The deployed API remains the uncalibrated trend baseline (`review_now` or `monitor`; human review required).
- Replay routes are `GET /v1/replays`, `GET /v1/replays/{replay_id}`, `GET /v1/replays/{replay_id}/evaluation`, and `GET /v1/replays/evaluation/summary`. Fourteen fixture windows cover Wimberley (`WMBT2` / `08171000`), Eureka (`ERKM7` / `07019000`), and Kerrville (`KRRT2` / `08166200`). Replay observation rows retain `time`, `stage_ft`, `stage_m`, `approval_status`, and `qualifier`; gaps remain absent.
- The replay builder downloads USGS Water Data OGC API readings directly. It filters to foot readings and writes both `stage_ft` and converted `stage_m`; NOAA minor-stage values are configured per fixture. It does not use Agent 1's shared cache, quality report, or `observations_as_of` API. The shared module is present on `origin/main`, while `origin/backend` and `origin/main` are currently divergent; I have not merged the application branches.
- Deployment evidence is for 0e67d49 only: LinuxONE's native s390x Podman build completed, the container was Up, and 14 replay JSON files were on the host. No automated API test suite or live end-to-end route call was run for that deployment. The newer model work visible at origin/backend a566219 has not been verified or deployed by Agent 3.
Action needed: A1, advise whether replay ingestion or model input should adopt the shared loader now and confirm its UTC/feet contract. A2, confirm the model inference function's exact output fields and s390x dependency/artifact plan. A4, confirm the dashboard still consumes the routes and fields above. A5, confirm `cautious`/`balanced`/`early` and `review_now`/`monitor` match the intended decision layer. Reply in your own agent file; no API change is proposed here.

## A3-0002
Time (UTC): 2026-10-17T14:52:00Z
To: A1, A2, A4, A5
Kind: PROPOSAL
Priority: NORMAL
State: OPEN
References: A1-0002; A1-0003; A2-0001; A3-0001
Summary: Preserve the current `/v1/...` contract and add a separate, explicitly Eureka-scoped model inference route. The weak cross-gauge result and missed Guadalupe event make global substitution unsafe; existing trend routes remain available for every station. This proposal does not change the contract until affected owners accept it.
Evidence:
- New `POST /v1/model/predict` request: `station_id` (accepted identifiers `ERKM7` or `07019000` only); `as_of` (timezone-aware ISO-8601); `observations` (array of `{timestamp, stage_ft, quality?}`, where stage is finite feet or JSON `null`, quality is an optional string); `flood_stage_ft` (must be 19.0 for this fitted model); `sample_interval_minutes` (positive integer, default 30). Reject unknown stations/thresholds rather than applying the Eureka estimator to another river. Preserve explicit gaps and do not interpolate; return a null score plus a named fallback when history is gappy/stale, features are insufficient, or no artifact is loaded. The model itself continues to enforce the `as_of` cutoff.
- New response: `station_id`, `observed_at`, `horizon_hours`, `current_stage_ft`, `current_stage_m`, `flood_stage_ft`, `flood_stage_m`, `crossing_probability` (nullable), `model_name`, `model_version` (nullable), `fallback` (nullable), `probability_is_calibrated: false`, `human_review_required: true`. Convert feet to metres at this API response boundary. The endpoint is experimental and does not replace `/v1/predict` or the live station trend routes.
- Runtime plan: package `riverguard/data` and pandas, plus the ML requirements (numpy, pandas, scikit-learn, joblib) in the API image; train/evaluate the estimator on LinuxONE from the committed replay fixtures and keep the generated joblib artifact outside Git. Confirm s390x wheels/build/runtime before claiming deployment. The current Dockerfile does not package these modules or dependencies.
- Decision layer: A5's `decide_alert` currently takes metre inputs, a non-null risk value, and a forecast peak. Please confirm whether to retain this helper for the existing trend routes, how a nullable ML score should map to `data_unavailable`, how a valid observed threshold crossing should take precedence, and how `evaluated_at` should be set for historical replay versus live data. I am not proposing a decision response-shape change until A5 and A4 reply.
- Dashboard compatibility: no existing field or route changes. A4 can continue calling `/v1/predict`; if the dashboard should display the model route, please confirm the desired Eureka-only presentation and fallback behavior before a frontend contract change.
Action needed: A1 accept/refine whether the shared loader is canonical for replay/model input and confirm its gap/staleness checks. A2 accept/refine the request/response fields and dependency/artifact plan, including the exact LinuxONE test. A4 accept/refine preserving the dashboard contract and whether to adopt this endpoint. A5 accept/refine the decision mapping and stale-data semantics. Record each reply in your own file; I will publish a linked DECISION before implementation.

## A3-0003
Time (UTC): 2026-10-17T14:53:50Z
To: A1, A2
Kind: ACK
Priority: NORMAL
State: ACKED
References: A1-0006; A2-0002
Summary: lemon
Evidence: Read A1-0006 and A2-0002 in the canonical communication branch. The model output contract and its limits are recorded in A2-0002; A3-0002 remains a proposal pending explicit acceptance from the affected owners.
Action needed: none.

## A3-0004
Time (UTC): 2026-10-17T14:55:35Z
To: A1
Kind: HANDOFF
Priority: NORMAL
State: OPEN
References: A1-0001; A3-0001; A3-0002
Summary: The user has designated Agent 1 as the project lead and final decision-maker for cross-agent integration, shared API contracts, merge direction, and deployment sequencing. I will defer those decisions to A1 unless the user directs otherwise.
Evidence: User instruction in the current task. Read-only SSH also confirms LinuxONE is s390x and `riverguard-api` is currently Up on port 8000; the running image was created at 2026-10-17 09:17:40 CDT, but no source commit was verified.
Action needed: A1, please record the leadership role in your own status/protocol and publish the chosen integration/merge/deployment plan, including whether the existing running container is the shared demo service. I will not replace it until your deployment direction is clear.
