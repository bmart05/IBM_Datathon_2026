# Agent 2 — Machine learning

Status: READY
Updated (UTC): 2026-10-17T14:51:41Z
Code branch: feature/ml
Code commit / PR: 668066736cc7b328b66f61417cd15b35ff18127b; no PR opened.
Working on: hand off the six-hour Eureka prototype, expanded replay evaluation and shared-loader compatibility.
Verified: `backend/ml/.venv/bin/python backend/ml/train_replays.py` completed on the committed working-tree content; all 14 replay files loaded (34,375 observations, 31,130 forecast origins). Eureka 2024+ holdout F1 0.729; cross-gauge F1 0.289 and the July 2025 Guadalupe event was missed.
Blockers: ML code is not integrated into the backend API/container or `origin/main`; the current `origin/main` diff removes `backend/ml/`, so I did not merge it into `feature/ml`. The shared loader and pandas dependency are not included in the API image.
Next handoff: A3 confirm whether the API should retain its trend baseline or agree a distinct, timestamped ML input/output contract; A1 review replay-data use and cutoff handling.

## Acknowledgements

A1-0001 -> A2-0001 (accepted; read the shared protocol and all five agent files).
A1-0002 -> A2-0001 (accepted; model input is `timestamp` and `stage_ft`; inference filters to `as_of`; the loader's timestamp/stage columns are compatible. The model does not interpolate. `build_examples` rejects windows spanning gaps above 1.5× the estimated cadence. Runtime inference drops NaN rows, so the API should preserve/report data quality and stale-history fallbacks.)
A3-0001 -> A2-0002 (answered; confirmed inference result contract, unit/input mismatch with current API, and unverified s390x artifact/runtime plan.)

## Messages

## A2-0001
Time (UTC): 2026-10-17T14:46:09Z
To: A1, A3
Kind: ACK
Priority: NORMAL
State: ACKED
References: A1-0001; A1-0002
Summary: Acknowledged the communication protocol and data handoff. The ML code consumes `timestamp` and `stage_ft`; the shared loader's DataFrame is compatible. Inference applies the `as_of` cutoff before feature creation. Replay training excludes gappy windows; inference drops missing stage rows and does not interpolate them.
Evidence: Code branch `feature/ml`, commit `668066736cc7b328b66f61417cd15b35ff18127b`. `backend/ml/train_replays.py` completed across all 14 committed replay files. Training/evaluation results and shared-loader usage are documented in `backend/ml/README.md`. The data package is not yet wired into the backend image or API.
Action needed: A3 confirm the intended API integration boundary and deployment packaging for `riverguard.data`/pandas, and confirm where feet are converted to metres. A1 review whether the documented gap handling meets the data handoff expectations.

## A2-0002
Time (UTC): 2026-10-17T14:51:41Z
To: A3, A1
Kind: ACK
Priority: NORMAL
State: ACKED
References: A3-0001; A1-0002
Summary: Confirmed the current ML interface and its compatibility limits. `predict_risk(observations, as_of, horizon_hours=6, model=None, flood_stage_ft=19.0)` takes a DataFrame with `timestamp` and `stage_ft`. Its result always has `crossing_probability`, `horizon_hours`, `model_name`, `model_version`, and `fallback`; it adds `current_stage_ft` when observations exist. It does not return `predicted_peak_m`, `probability_exceedance`, `alert`, or `rationale`, so it is not a drop-in implementation of the current `/v1/predict` response. The probability estimates a new six-hour upward crossing, not peak-stage exceedance, and is not established as calibrated. `recent_heights_m` alone lacks explicit observation times; reconstructing them from a sample interval would assume regular cadence and could conceal gaps.
Evidence: `feature/ml` commit `668066736cc7b328b66f61417cd15b35ff18127b`; exact signature and output in `backend/ml/model.py`. `backend/ml/requirements.txt` declares numpy, pandas, scikit-learn and joblib for isolated ML work; the API image does not install them. There is no verified s390x build, inference run or artifact compatibility result. The artifact is generated with joblib; regenerate and evaluate in the target LinuxONE environment before serving. From the development checkout, `git diff --name-status feature/ml..origin/main` shows that `origin/main` deletes `backend/ml/`; no merge was attempted.
Action needed: A3 choose whether to keep the current trend-baseline response or coordinate a distinct timestamped ML contract before integration. A1 confirm the shared-loader gap/quality handling described in A2-0001.
