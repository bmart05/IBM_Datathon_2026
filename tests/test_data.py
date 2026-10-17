"""Offline data-contract and failure-path tests; no requests to USGS."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pandas as pd

from riverguard.data import (
    DataQualityError, DataUnavailableError, load_fixture, load_stage,
    observations_as_of, quality_report,
)
from riverguard.data import ingestion as ing
from riverguard.data.preprocessing import normalize_features


def feature(time="2024-11-04T00:00:00Z", value="10", **extra):
    return {"type": "Feature", "properties": {
        "monitoring_location_id": "USGS-07019000", "parameter_code": "00065",
        "time": time, "value": value, "unit_of_measure": "ft",
        "approval_status": "Approved", "qualifier": None, **extra,
    }}


def page(features, links=None, **extra):
    return {"type": "FeatureCollection", "features": features, "links": links or [], **extra}


class NormalizationTests(unittest.TestCase):
    def test_contract_units_sorting_and_timezones(self):
        frame = normalize_features([
            feature("2024-11-04T01:00:00Z", "20"),
            feature("2024-11-03T18:00:00-06:00", "3.048", unit_of_measure="m", qualifier="Estimated"),
        ], "07019000")
        self.assertEqual(list(frame.columns), ["station_id", "timestamp", "stage_ft", "quality"])
        self.assertEqual(str(frame.timestamp.dt.tz), "UTC")
        self.assertEqual(frame.station_id.tolist(), ["07019000"] * 2)
        self.assertAlmostEqual(frame.stage_ft.iloc[0], 10)
        self.assertEqual(frame.quality.iloc[0], "Approved;Estimated")
        self.assertTrue(frame.timestamp.is_monotonic_increasing)

    def test_missing_and_nonfinite_values_are_explicit(self):
        for value in (None, "Ice", "-999999", "NaN", "inf"):
            with self.subTest(value=value):
                frame = normalize_features([feature(value=value)], "07019000")
                self.assertTrue(pd.isna(frame.stage_ft.iloc[0]))
                self.assertIn("missing_value", frame.quality.iloc[0])

    def test_same_value_duplicates_combine_quality(self):
        frame = normalize_features([feature(), feature(approval_status="Provisional")], "07019000")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.attrs["duplicates_removed"], 1)
        self.assertIn("Approved", frame.quality.iloc[0])
        self.assertIn("Provisional", frame.quality.iloc[0])

    def test_conflicting_duplicates_fail(self):
        for second in ("11", None):
            with self.subTest(second=second), self.assertRaises(DataQualityError):
                normalize_features([feature(), feature(value=second)], "07019000")

    def test_wrong_series_units_and_timestamps_fail(self):
        for extra in ({"unit_of_measure": "cfs"}, {"parameter_code": "00060"},
                      {"monitoring_location_id": "USGS-08171000"}, {"time": "bad"}, {"time": None}):
            with self.subTest(extra=extra), self.assertRaises(DataQualityError):
                normalize_features([feature(**extra)], "07019000")

    def test_gap_outlier_report_does_not_fill_or_drop(self):
        frame = normalize_features([feature(), feature("2024-11-04T02:00:00Z", "120")], "07019000")
        report = quality_report(frame, expected_interval_minutes=15)
        self.assertEqual(report["gaps"][0]["duration_minutes"], 120)
        self.assertEqual(len(report["extreme_stage_times"]), 1)
        self.assertEqual(len(report["rapid_change_times"]), 1)
        self.assertEqual(len(frame), 2)

    def test_as_of_is_inclusive_and_future_independent(self):
        past = normalize_features([feature()], "07019000")
        full = normalize_features([feature(), feature("2024-11-04T01:00:00Z", "99")], "07019000")
        pd.testing.assert_frame_equal(observations_as_of(full, "2024-11-04"), past)

    def test_empty_frame_and_invalid_report_config(self):
        frame = normalize_features([], "07019000")
        self.assertEqual(quality_report(frame)["observation_count"], 0)
        self.assertEqual(str(frame.timestamp.dt.tz), "UTC")
        with self.assertRaises(ValueError):
            quality_report(frame, expected_interval_minutes=0)

    def test_requested_boundary_gaps_reported(self):
        frame = normalize_features([feature()], "07019000")
        frame.attrs["query"] = {"start": "2024-11-03", "end": "2024-11-05"}
        report = quality_report(frame, expected_interval_minutes=15)
        self.assertEqual([g["boundary"] for g in report["coverage_gaps"]], ["start", "end"])

    def test_cutoff_does_not_report_future_coverage_as_missing(self):
        frame = normalize_features([feature()], "07019000")
        frame.attrs["query"] = {"start": "2024-11-04", "end": "2024-11-05"}
        past = observations_as_of(frame, "2024-11-04")
        self.assertEqual(quality_report(past)["coverage_gaps"], [])
        self.assertEqual(frame.attrs["query"]["end"], "2024-11-05")


class IngestionTests(unittest.TestCase):
    def setUp(self):
        ing.DEFAULT_CACHE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ing.DEFAULT_CACHE)
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)

    def load(self, **kwargs):
        return load_stage("07019000", "2024-11-04", "2024-11-05", cache_dir=self.cache, **kwargs)

    @patch.object(ing, "_fetch_json")
    def test_all_pages_loaded_sorted_and_clipped(self, fetch):
        next_url = ing.API + "?offset=1&f=json"
        fetch.side_effect = [page([feature("2024-11-04T01:00:00Z")], [{"rel": "next", "href": next_url}]),
                             page([feature(), feature("2024-11-06T00:00:00Z")])]
        frame = self.load()
        self.assertEqual(len(frame), 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(frame.timestamp.is_monotonic_increasing)
        self.assertEqual(len(frame.attrs["source_urls"]), 2)
        self.assertFalse(frame.attrs["cache_hit"])

    @patch.object(ing, "_fetch_json", return_value=page([feature()]))
    def test_cache_offline_and_equivalent_utc_bounds(self, fetch):
        first = self.load()
        second = load_stage("USGS-07019000", "2024-11-03T18:00:00-06:00", "2024-11-05T00:00:00Z",
                            cache_dir=self.cache, offline=True)
        pd.testing.assert_frame_equal(first, second)
        self.assertTrue(second.attrs["cache_hit"])
        self.assertEqual(fetch.call_count, 1)

    @patch.object(ing, "_fetch_json")
    def test_cache_miss_does_not_call_network(self, fetch):
        with self.assertRaises(DataUnavailableError):
            self.load(offline=True)
        fetch.assert_not_called()

    @patch.object(ing, "_fetch_json", return_value=page([feature()]))
    def test_refresh_failure_preserves_existing_cache(self, fetch):
        original = self.load()
        fetch.side_effect = DataUnavailableError("offline")
        with self.assertRaises(DataUnavailableError):
            self.load(refresh=True)
        pd.testing.assert_frame_equal(original, self.load(offline=True))

    @patch.object(ing, "_fetch_json")
    def test_invalid_request_rejected_before_network(self, fetch):
        for station, start, end in [("abc", "2024-11-04", "2024-11-05"),
                                    ("07019000", "2024-11-06", "2024-11-04"),
                                    ("07019000", "NaT", "2024-11-04")]:
            with self.subTest(station=station, start=start), self.assertRaises(ValueError):
                load_stage(station, start, end, cache_dir=self.cache)
        with self.assertRaises(ValueError):
            self.load(offline=True, refresh=True)
        fetch.assert_not_called()

    @patch.object(ing, "_fetch_json")
    def test_empty_malformed_and_incomplete_responses_not_cached(self, fetch):
        for response in ({}, page([]), page([feature()], numberReturned=2),
                         page([feature()], numberMatched=10)):
            with self.subTest(response=response):
                fetch.return_value = response
                with self.assertRaises(DataUnavailableError):
                    self.load()
                self.assertEqual(list(self.cache.glob("*.json")), [])

    @patch.object(ing, "_fetch_json")
    def test_loop_and_untrusted_pagination_rejected(self, fetch):
        for next_url in ("https://example.com/leak", "https://api.waterdata.usgs.gov/unexpected"):
            fetch.return_value = page([feature()], [{"rel": "next", "href": next_url}])
            with self.assertRaises(DataUnavailableError):
                self.load()
        fetch.return_value = page([feature()], [{"rel": "next", "href": "?repeat=1"}])
        with self.assertRaisesRegex(DataUnavailableError, "loop"):
            self.load()

    @patch.object(ing, "_fetch_json", return_value=page([feature()]))
    def test_corrupt_or_mismatched_cache_rejected(self, fetch):
        self.load()
        path = next(self.cache.glob("*.json"))
        record = json.loads(path.read_text())
        record["query"]["station_id"] = "08171000"
        path.write_text(json.dumps(record))
        with self.assertRaises(DataUnavailableError):
            self.load(offline=True)
        path.write_text("{bad")
        with self.assertRaises(DataUnavailableError):
            self.load(offline=True)

    @patch.object(ing.time, "sleep")
    @patch.object(ing, "urlopen")
    def test_transient_errors_retried_with_bound(self, open_url, sleep):
        open_url.side_effect = URLError("temporary")
        with self.assertRaises(DataUnavailableError):
            ing._fetch_json(ing.API)
        self.assertEqual(open_url.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(ing.time, "sleep")
    @patch.object(ing, "urlopen")
    def test_permanent_http_failure_not_retried(self, open_url, sleep):
        open_url.side_effect = HTTPError(ing.API, 404, "Not found", {}, None)
        with self.assertRaisesRegex(DataUnavailableError, "404"):
            ing._fetch_json(ing.API)
        self.assertEqual(open_url.call_count, 1)
        sleep.assert_not_called()


class RealFixtureTests(unittest.TestCase):
    @patch.object(ing, "urlopen", side_effect=AssertionError("Network access forbidden"))
    def test_source_fixture_contract_and_known_samples(self, request):
        frame = load_fixture()
        self.assertEqual(len(frame), 571)
        self.assertAlmostEqual(frame.stage_ft.max(), 35.39)
        self.assertEqual(frame.loc[frame.stage_ft.idxmax(), "timestamp"], pd.Timestamp("2024-11-08T13:30:00Z"))
        self.assertTrue(frame.timestamp.is_monotonic_increasing)
        self.assertFalse(frame.timestamp.duplicated().any())
        self.assertEqual(frame.station_id.unique().tolist(), ["07019000"])
        report = quality_report(frame, expected_interval_minutes=15)
        self.assertEqual(len(report["gaps"]), 2)
        self.assertEqual(report["missing_values"], 0)
        self.assertTrue(frame.attrs["is_fixture"])
        self.assertIn("api.waterdata.usgs.gov", frame.attrs["source_urls"][0])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
