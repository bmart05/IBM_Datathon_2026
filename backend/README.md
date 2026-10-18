# RiverGuard backend

The FastAPI service keeps the existing NOAA trend endpoints and source replay fixtures, and adds a separate Eureka-only experimental classifier contract. The trend heuristic and the classifier's six-hour new-crossing score are different outputs. Neither is a calibrated operational forecast; decisions require human review.

## Run locally

From the repository root, build and run the API image:

```sh
podman build -f backend/Dockerfile -t riverguard-api:candidate .
podman run --rm -p 8000:8000 riverguard-api:candidate
```

Open `http://localhost:8000/docs`. The image needs the complete repository as its build context because it packages `backend/app`, replay fixtures, `backend/ml`, and the shared `riverguard/data` module.

## API routes

The existing `GET /v1/stations`, `GET /v1/stations/{identifier}`, `GET /v1/stations/{identifier}/predict`, `POST /v1/predict`, and historic replay/evaluation endpoints remain available. NOAA stage and flow values are converted to metres and cubic metres per second.

`POST /v1/replays/{replay_id}/predict` evaluates the existing linear-trend baseline against the server-side fixture at a supplied historical clock. Send `{ "as_of": "2024-11-05T19:45:00Z", "policy": "balanced" }`. The endpoint uses only fixture observations at or before `as_of`, preserves actual timestamps, and reports missing, unapproved, gappy, stale, or conflicting history as `decision_state: "data_unavailable"` with a named `fallback`. A valid response includes the baseline `predicted_peak_m` and `probability_exceedance` plus `decision_state`, `policy_threshold`, `rationale`, and data-quality details. The trend score is explicitly uncalibrated.

`POST /v1/model/predict` accepts timestamped Eureka observations with `stage_ft`, approval status, and qualifier, together with `as_of`, `sample_interval_minutes`, `flood_stage_ft: 19`, and an optional policy. Only `ERKM7` / `07019000` is supported. The six-hour ML history must be complete, fresh at the declared cadence, and source-approved; rows after `as_of` cannot affect the result. `crossing_probability` is an experimental score for a new upward crossing, never a predicted peak and never the trend endpoint's `probability_exceedance`. Responses mark it uncalibrated and require human review. A missing artifact or unusable history returns a null score and a named fallback. The separately reported measured stage may still show an observed threshold exceedance when its timestamp and source quality are valid.

The model runtime loads its artifact from `/srv/riverguard/artifacts/ml/USGS-07019000` by default. Set `RIVERGUARD_MODEL_DIR` to override that directory. Keep generated artifacts outside Git and regenerate them inside the target `s390x` runtime after the ML owner’s canonical training/serving import fix is merged. The ML README records the authoritative training command and artifact metadata.

## LinuxONE candidate

The LinuxONE image uses Python 3.12 and pins NumPy 2.5.4, pandas 3.0.6, scikit-learn 1.9.1, and joblib 1.6.0. PyPI did not provide compatible s390x wheels for the scientific packages during the check; an isolated disposable LinuxONE container successfully built and imported these versions from source with `build-essential`, `gfortran`, OpenBLAS, LAPACK, and `pkg-config`. The candidate image includes those build prerequisites so the result can be reproduced on the target architecture.

Sync the repository root to a separate candidate directory on the server. From that directory, build with:

```sh
podman build -f backend/Dockerfile -t riverguard-api:candidate .
```

After generating the ML artifact inside the `s390x` image using the current `backend/ml/README.md` instructions, start the candidate bound only to loopback port 8002 and mount the artifact directory read-only:

```sh
podman run -d --name riverguard-api-candidate --restart=unless-stopped \
  -p 127.0.0.1:8002:8000 \
  -e RIVERGUARD_MODEL_DIR=/srv/riverguard/artifacts/ml/USGS-07019000 \
  -v ~/riverguard-artifacts:/srv/riverguard/artifacts:ro \
  riverguard-api:candidate
```

Check `http://127.0.0.1:8002/health` on the server, then tunnel it with `ssh -L 8002:127.0.0.1:8002 linux1@148.100.77.165` and open `http://localhost:8002/docs`. The existing shared service on port 8000 stays running while the candidate is built and tested. Replacing that shared service is a separate promotion step and requires the existing human approval.

Stop or remove only the candidate container to roll it back:

```sh
podman stop riverguard-api-candidate
podman rm riverguard-api-candidate
```

The candidate image and actual model inference still need to be built and verified on `s390x` after the reviewed ML package is available. A successful dependency source-build check alone does not count as an API candidate or model-inference test.
