from datetime import datetime, timedelta, timezone
import hashlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


DEMO_DIR = Path(__file__).resolve().parents[2] / "demo"
sys.path.insert(0, str(DEMO_DIR))
from compare_policies import compare_replay_data, validate_trend_window  # noqa: E402
from eureka_crossing_decisions import evaluation_report  # noqa: E402


START = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def observation(offset_minutes: int, stage_m: float | None = 1.0) -> dict:
    return {
        "time": (START + timedelta(minutes=offset_minutes)).isoformat(),
        "stage_m": stage_m,
        "approval_status": "Approved",
        "qualifier": None,
    }


def replay(rows: list[dict]) -> dict:
    return {
        "replay_id": "synthetic-gap-regression",
        "target": {"threshold_m": 2.0},
        "source": {"name": "synthetic regression fixture"},
        "observations": rows,
    }


class TrendPolicyHistoryTests(unittest.TestCase):
    def test_gappy_window_is_reported_unavailable_and_not_forecast(self):
        rows = [observation(offset) for offset in (0, 15, 45, 60)]
        result = compare_replay_data(replay(rows))

        self.assertEqual(result["evaluated_origins"], 0)
        self.assertGreater(result["unavailable_origins_by_reason"].get("gappy_history", 0), 0)
        self.assertEqual(sum(map(sum, result["decision_counts"].values())), 0)

    def test_null_or_non_finite_stage_is_reported_unavailable(self):
        for invalid_stage in (None, float("nan")):
            with self.subTest(stage=invalid_stage):
                rows = [observation(offset, 1.0) for offset in (0, 15, 30, 45, 60)]
                rows[2]["stage_m"] = invalid_stage
                result = compare_replay_data(replay(rows))
                expected = "missing_observations" if invalid_stage is None else "invalid_observations"
                self.assertGreater(result["unavailable_origins_by_reason"].get(expected, 0), 0)

    def test_exact_gap_limit_is_accepted_and_future_rows_are_excluded(self):
        history = [observation(offset) for offset in (0, 15, 37.5, 60)]
        history.append(observation(75, 9.0))
        values, reason = validate_trend_window(
            history,
            as_of=START + timedelta(minutes=60),
            cadence_minutes=15,
        )
        self.assertIsNone(reason)
        self.assertEqual(len(values), 4)
        self.assertEqual(values[-1][1], 1.0)

    def test_conflicting_duplicate_times_are_rejected(self):
        rows = [observation(offset) for offset in (0, 15, 30, 45, 60)]
        rows.append(dict(rows[-1], stage_m=1.1))
        result = compare_replay_data(replay(rows))
        self.assertGreater(result["unavailable_origins_by_reason"].get("conflicting_duplicates", 0), 0)

    def test_evaluation_metrics_are_loaded_with_report_and_source_hashes(self):
        self.assertEqual(evaluation_report(None)["status"], "unavailable")
        with TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            model_path = directory / "model.joblib"
            model_path.write_bytes(b"test model")
            metadata_path = directory / "model_metadata.json"
            metadata_path.write_text(json.dumps({
                "model_name": "hist_gradient_boosting",
                "model_version": "v2",
                "training_data_id": "sha256:training-set",
                "train_end": "2024-01-01T00:00:00+00:00",
            }))
            report = {
                "evaluation": {"primary_eureka": {"f1": 0.8}},
                "source_checksums_sha256": {"replay.json": "abc123"},
                "artifact": {"model_path": str(model_path), "metadata_path": str(metadata_path)},
            }
            raw = json.dumps(report).encode()
            path = directory / "evaluation.json"
            path.write_bytes(raw)
            loaded = evaluation_report(path)
        self.assertEqual(loaded["status"], "reported")
        self.assertEqual(loaded["report_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(loaded["source_checksums_sha256"], {"replay.json": "abc123"})
        self.assertEqual(loaded["eureka_holdout_metrics"], {"f1": 0.8})
        self.assertEqual(loaded["artifact_identity"]["model_metadata"]["model_version"], "v2")
        self.assertEqual(
            loaded["artifact_identity"]["files"]["model_path"]["sha256"],
            hashlib.sha256(b"test model").hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
