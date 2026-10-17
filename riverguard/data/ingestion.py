"""USGS continuous observations, complete pagination and explicit offline caching."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

import pandas as pd

from .preprocessing import DataQualityError, normalize_features, station_number, utc_timestamp

API = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/continuous/items"
DEFAULT_CACHE = Path(__file__).parent / ".cache"


class DataUnavailableError(RuntimeError):
    """Network, cache, or source data unavailable; no fabricated fallback."""


def _query(station_id: str, start: str, end: str) -> dict:
    start, end = utc_timestamp(start), utc_timestamp(end)
    if start > end:
        raise ValueError("start must be no later than end")
    return {"station_id": station_number(station_id), "start": start.isoformat(), "end": end.isoformat()}


def cache_path(query: dict, cache_dir: Path) -> Path:
    digest = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()[:24]
    return Path(cache_dir) / f"{query['station_id']}-{digest}.json"


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "RiverGuard/0.1"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                raise DataUnavailableError(f"USGS HTTP {exc.code}") from exc
            failure = exc
        except (URLError, TimeoutError, OSError) as exc:
            failure = exc
        except (ValueError, UnicodeError) as exc:
            raise DataUnavailableError("USGS returned invalid JSON") from exc
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise DataUnavailableError(f"USGS request failed after three attempts: {failure}") from failure


def _download(query: dict) -> dict:
    url = API + "?" + urlencode({
        "monitoring_location_id": f"USGS-{query['station_id']}", "parameter_code": "00065",
        "datetime": f"{query['start']}/{query['end']}", "f": "json", "limit": 10000,
    })
    pages, seen, urls = [], set(), []
    while url:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "api.waterdata.usgs.gov" or parts.path != urlsplit(API).path:
            raise DataUnavailableError("Unexpected USGS pagination URL")
        if url in seen or len(seen) >= 1000:
            raise DataUnavailableError("USGS pagination loop or excessive number of pages")
        seen.add(url)
        page = _fetch_json(url)
        if not isinstance(page, dict) or page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise DataUnavailableError("USGS response is not a GeoJSON feature collection")
        if page.get("numberReturned", len(page["features"])) != len(page["features"]):
            raise DataUnavailableError("Incomplete USGS page")
        pages.append(page)
        urls.append(url)
        next_links = [link["href"] for link in page.get("links", []) if link.get("rel") == "next"]
        if len(next_links) > 1:
            raise DataUnavailableError("Ambiguous USGS pagination")
        url = urljoin(url, next_links[0]) if next_links else None
    matched = pages[0].get("numberMatched")
    if isinstance(matched, int) and matched != sum(len(page["features"]) for page in pages):
        raise DataUnavailableError("USGS returned fewer/more records than numberMatched")
    return {"schema_version": 1, "query": query, "source": "USGS continuous gage height (00065)",
            "source_urls": urls, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "license": "USGS U.S. government data; public domain", "pages": pages}


def _frame(record: dict, query: dict) -> pd.DataFrame:
    if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("query") != query:
        raise DataUnavailableError("Cache metadata does not match the requested interval")
    pages = record.get("pages")
    if not isinstance(pages, list) or not pages or any(not isinstance(p, dict) or not isinstance(p.get("features"), list) for p in pages):
        raise DataUnavailableError("Invalid cache pages")
    if not all(key in record for key in ("source", "source_urls", "retrieved_at", "license")):
        raise DataUnavailableError("Cache lacks source provenance")
    frame = normalize_features([f for page in pages for f in page["features"]], query["station_id"])
    frame = frame.loc[frame["timestamp"].between(utc_timestamp(query["start"]), utc_timestamp(query["end"]))].copy()
    if frame.empty:
        raise DataUnavailableError("No stage observations in the requested interval")
    frame.attrs.update({k: record[k] for k in ("source", "source_urls", "retrieved_at", "query", "license")})
    return frame.reset_index(drop=True)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataUnavailableError(f"Cannot read data cache {path}; refresh it while online") from exc


def load_stage(station_id: str, start: str, end: str, *, cache_dir: str | Path | None = None,
               offline: bool = False, refresh: bool = False) -> pd.DataFrame:
    """Return station_id, timestamp (UTC), stage_ft, quality in time order.

    Bounds are inclusive; date-only and naive timestamps mean midnight UTC.
    Cache is exact-interval and read-first, with retrieval metadata in df.attrs.
    Refresh explicitly to request revised observations. Offline never uses HTTP;
    failed refreshes raise and leave any existing cache intact.
    """
    if offline and refresh:
        raise ValueError("offline and refresh cannot both be enabled")
    query = _query(station_id, start, end)
    directory = Path(cache_dir) if cache_dir is not None else Path(os.getenv("RIVERGUARD_DATA_CACHE", str(DEFAULT_CACHE)))
    path = cache_path(query, directory)
    if path.exists() and not refresh:
        frame = _frame(_read(path), query)
        frame.attrs["cache_hit"] = True
        return frame
    if offline:
        raise DataUnavailableError(f"No cache for this exact station/interval: {path}")
    record = _download(query)
    frame = _frame(record, query)  # Validate before persisting; never cache malformed responses.
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    frame.attrs["cache_hit"] = False
    return frame


def load_fixture(path: str | Path | None = None) -> pd.DataFrame:
    """Read the committed, source-labelled historical sample without network IO."""
    path = Path(path) if path is not None else Path(__file__).parent / "fixtures" / "eureka-nov-2024.json"
    record = _read(path)
    if not isinstance(record, dict) or not isinstance(record.get("query"), dict):
        raise DataUnavailableError("Invalid fixture metadata")
    frame = _frame(record, record.get("query", {}))
    frame.attrs["is_fixture"] = True
    return frame
