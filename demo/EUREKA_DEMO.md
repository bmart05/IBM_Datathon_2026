# Eureka replay demo

This walkthrough keeps observed readings, the model score, and policy outcomes distinct. The decision helper and replay comparator do not run the classifier.

## Prepare a source-hashed model report

From `backend/ml/`, train and save the JSON evaluation output outside the repository:

```sh
python train_replays.py > /tmp/riverguard-evaluation.json
```

The report loader requires `evaluation.primary_eureka` and `source_checksums_sha256`. It prints the report SHA-256, replay-source hashes, and—when the paths are accessible—the model artifact and metadata hashes plus model version/training-data ID. If no report is supplied, the demo labels model evaluation unavailable instead of reusing hardcoded metrics.

## Run the three committed Eureka points

Once Agent 3's isolated candidate is reachable on loopback port 8002, run from `backend/`:

```sh
python ../demo/eureka_crossing_decisions.py \
  --evaluation-report /tmp/riverguard-evaluation.json \
  --api-base-url http://127.0.0.1:8002
```

The script sends only timestamped fixture rows at or before each replay time to `POST /v1/model/predict`:

| Case | `as_of` (UTC) | Expected evidence |
|---|---|---|
| Below threshold | `2024-11-05T19:45:00Z` | 18.96 ft, Approved; 25 readings in the six-hour window and 26 rows including the lag sample; A2 validator d5646a2 returns valid with 15-minute max gap. The next recorded threshold crossing is at 20:00Z. |
| Observed crossing | `2024-11-05T20:00:00Z` | 19.04 ft, Approved; A2 validator d5646a2 returns valid with 15-minute max gap; report as a measured threshold exceedance, not a model prediction. |
| Gappy history | `2024-11-03T10:30:00Z` | Latest stage 3.03 ft is Approved; 25 request rows include a 30-minute interval against 15-minute cadence; A2 validator d5646a2 returns `gappy_history`. |

The replay fixture SHA-256 at the source fixture is `ec77389e7ac425896a8d17ebc9808e710f222d87ea66e4816f838ac48fb622a9`; the script computes it at runtime. The JSON also includes the source URL, approval/qualifier, request count, HTTP status, raw endpoint response and model name/version returned by the candidate. Without `--api-base-url`, no endpoint or deployment success is implied. Without `--evaluation-report`, the evaluation section says unavailable. The balanced-policy example uses a clearly labelled illustrative 0.60 score and is not model inference.

## Run the gap-aware trend comparison and tests

From `backend/`:

```sh
python ../demo/compare_policies.py
python -m unittest discover -s tests -v
```

The comparator reports unavailable origins by reason and excludes malformed, missing, duplicate, unapproved, qualified, or gappy one-hour windows rather than fitting across them. Its policy counts describe sensitivity of the existing trend heuristic; they are not classifier validation.
