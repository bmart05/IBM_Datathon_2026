"""Download a reproducible window: python -m riverguard.data --help."""
import argparse
import json
from pathlib import Path

from .ingestion import DEFAULT_CACHE, cache_path, load_stage
from .preprocessing import quality_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="07019000")
    parser.add_argument("--start", required=True, help="Inclusive UTC bound")
    parser.add_argument("--end", required=True, help="Inclusive UTC bound (a date means midnight)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--fixture", type=Path, help="Copy the provenance-labelled source cache to this JSON file")
    parser.add_argument("--csv", type=Path, help="Optional normalized CSV export")
    parser.add_argument("--cadence-minutes", type=float, default=15)
    args = parser.parse_args()
    frame = load_stage(args.station, args.start, args.end, cache_dir=args.cache_dir,
                       offline=args.offline, refresh=args.refresh)
    if args.fixture:
        args.fixture.parent.mkdir(parents=True, exist_ok=True)
        args.fixture.write_bytes(cache_path(frame.attrs["query"], args.cache_dir).read_bytes())
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.csv, index=False)
    report = quality_report(frame, expected_interval_minutes=args.cadence_minutes)
    report["source_urls"] = frame.attrs["source_urls"]
    report["retrieved_at"] = frame.attrs["retrieved_at"]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
