# Agent 1 — Data engineering

Status: READY
Updated (UTC): 2026-10-17T14:41:48Z
Code branch: feature/data
Code commit / PR: 00d55d8ec4b9af05c9d6f282da15c55227fac732; https://github.com/bmart05/IBM_Datathon_2026/pull/2 (merged into main as 77f9cb460061a605c7dce50d461b99500f965820)
Working on: establish this communication hub and support data integration.
Verified: `python -m pytest tests/test_data.py -q` — 21 passed, 19 subtests passed on the data commit; offline CLI replay passed. Three historical windows fetched; fixture visually reviewed; independent USGS/NOAA cross-check recorded.
Blockers: no data-component blocker; waiting for other agents to acknowledge the handoffs below.
Next handoff: A2/A3 consume the shared loader or explicitly describe their existing adapter and any remaining gaps.

## Acknowledgements

No peer replies received at bootstrap. Do not mark requests acknowledged until a recipient posts a linked reply.

## A1-0001
Time (UTC): 2026-10-17T14:41:48Z
To: ALL
Kind: HANDOFF
Priority: NORMAL
State: OPEN
References: coordination/README.md
Summary: This branch is the canonical Git-based agent communication channel, created at the user's request.
Evidence: Read the shared protocol; each agent has one owned file to avoid concurrent README edits. Branch activity alone is not acknowledgement.
Action needed: A2, A3, A4, A5 — acknowledge this ID in your own file and publish actual status, code branch/commit, tests and blockers. Report any existing competing communication channel so we can reconcile it. Do not create another branch for communication.

## A1-0002
Time (UTC): 2026-10-17T14:41:48Z
To: A2
Kind: HANDOFF
Priority: NORMAL
State: OPEN
References: A1-0001; PR #2
Summary: Shared data loader and verified offline sample are merged into main.
Evidence: `from riverguard.data import load_stage, load_fixture, observations_as_of, quality_report`. Contract columns are station_id/string, timestamp/UTC, stage_ft/float, quality/string. `load_fixture()` yields 571 real Eureka readings for 4–10 November 2024, including two explicit gaps. More detail: riverguard/data/README.md on main.
Action needed: Confirm what your model consumes and its branch/commit; preserve the as_of cutoff, inspect gaps, and report train/evaluation periods and actual metrics. If you already use another verified USGS adapter, describe the mapping instead of duplicating it. Archive data may contain later revisions; do not claim point-in-time publication availability.

## A1-0003
Time (UTC): 2026-10-17T14:41:48Z
To: A3
Kind: HANDOFF
Priority: NORMAL
State: OPEN
References: A1-0001; PR #2
Summary: Data component is ready; please confirm the backend integration boundary and deployment status.
Evidence: Shared module uses UTC and feet. The existing backend uses metre fields at some boundaries and `/v1/...` routes. The Dockerfile at bootstrap copies backend/app and backend/data, not the shared module, and the backend dependency list does not include pandas. Raw NaN values need conversion to JSON null.
Action needed: Report whether the shared loader is integrated or whether an existing verified adapter remains in use. If consuming the module, include it/pandas in the container and convert feet only at the API boundary. Confirm the canonical observation/prediction schema, supported stations and deployment evidence with A2/A4/A5. Only claim LinuxONE execution after verifying the actual runtime.

## A1-0004
Time (UTC): 2026-10-17T14:41:48Z
To: A4
Kind: QUESTION
Priority: NORMAL
State: OPEN
References: A1-0001
Summary: Please publish the dashboard's current API contract and pending integration needs.
Evidence: Repository frontend/backend already exist and use routes/payloads that differ from the original pasted plan. Data observations are historical, not synthetic; two November 2024 gaps must remain visible.
Action needed: Confirm API routes, stage units, timestamp format and supported replay IDs with A3. Clearly label observations versus forecast/simulation, preserve missing-data states, and report one actual end-to-end check or the blocker preventing it. Reply here via your own file.

## A1-0005
Time (UTC): 2026-10-17T14:41:48Z
To: A5
Kind: HANDOFF
Priority: NORMAL
State: OPEN
References: A1-0001
Summary: Verified Eureka observations, source links, crest cross-checks and data gaps are available for the decision/demo work.
Evidence: riverguard/data/fixtures/source-crosscheck.json on main records NOAA action/minor/moderate/major stages of 17/19/26/31 feet and the November 2024 crest of 35.39 feet at 13:30 UTC on 8 November. This is source reference material, not model performance evidence.
Action needed: Confirm alert policy names/schema with A3/A4; the existing backend uses cautious/balanced/early. Publish policy tests, human-review behavior, missing-data handling and replay/demo needs. Keep official stage thresholds distinct from configurable model-risk thresholds.
