# RiverGuard backend (starter)

Small FastAPI service intended for IBM LinuxONE. It includes `/health`, a `/v1/predict` trend-baseline endpoint, and NOAA integration for U.S. gauges. Wimberley (NOAA `WMBT2`, USGS `08171000`) remains the default demo station. It accepts the sensor sample interval. The returned risk number is explicitly marked uncalibrated; the baseline is for wiring the dashboard and proving deployment, and is **not a trained or calibrated flood model** and must not be used for operational decisions.

## Run locally

```sh
podman build -t riverguard-api:latest .
podman run -d --name riverguard-api --restart=unless-stopped -p 8000:8000 riverguard-api:latest
```

Then visit `http://localhost:8000/docs`. Use `GET /v1/stations?west=-100&south=25&east=-93&north=37` to list NOAA gauges in a map viewport (each bounding-box side must be at most 12 degrees). Then call `GET /v1/stations/{station_id}` for normalized observations, official forecast, and flood thresholds, or `GET /v1/stations/{station_id}/predict?policy=balanced` to run the baseline for that gauge. NOAA station IDs may be LIDs such as `WMBT2` or USGS IDs such as `08171000`. Stage and flow are converted to metres and cubic metres/second. The Wimberley shortcuts remain at `/v1/stations/wimberley` and `/v1/stations/wimberley/predict`. Larger areas should be requested as multiple map viewports, which keeps NOAA listing requests responsive. Historical replay endpoints are `GET /v1/replays`, `GET /v1/replays/{replay_id}`, and `GET /v1/replays/{replay_id}/evaluation`. Three Wimberley events are bundled with their timestamped USGS observations. The evaluation compares persistence with a one-hour linear trend for six-hour threshold-crossing windows and excludes incomplete windows. Overlapping predictions within one flood are correlated, so these metrics are descriptive and are not evidence of generalization. Rebuild all fixtures by running `python scripts/build_replays.py <replay-id> --output-dir data/replays` once for each ID returned by `GET /v1/replays`. The generic demo endpoint remains available; for example:

```sh
curl -sS http://localhost:8000/v1/predict \
  -H 'content-type: application/json' \
  -d '{"station_id":"demo-gauge","observed_at":"2026-10-17T12:00:00Z","recent_heights_m":[1.10,1.12,1.16,1.21,1.29,1.38,1.50],"flood_stage_m":1.8,"policy":"balanced"}'
```

## Deploy to LinuxONE

The host needs rootless Podman (available on the configured LinuxONE account). From this directory:

```sh
ssh -i ~/Downloads/yoyo12.pem linux1@148.100.77.165 'mkdir -p ~/riverguard-backend'
rsync -avz -e 'ssh -i ~/Downloads/yoyo12.pem' ./ linux1@148.100.77.165:~/riverguard-backend/
ssh -i ~/Downloads/yoyo12.pem linux1@148.100.77.165 'cd ~/riverguard-backend && podman build -t riverguard-api:latest .'
ssh -i ~/Downloads/yoyo12.pem linux1@148.100.77.165 'podman rm -f riverguard-api 2>/dev/null || true; podman run -d --name riverguard-api --restart=unless-stopped -p 8000:8000 riverguard-api:latest'
```

The process listens on server port 8000, but inbound 8000 is not allowed by the current host firewall. For a demo, create an SSH tunnel with `ssh -i ~/Downloads/yoyo12.pem -L 8001:127.0.0.1:8000 linux1@148.100.77.165`, then open `http://localhost:8001/docs` (port 8000 is already occupied locally). To make it directly reachable, add an inbound firewall/security-group rule for the demo client and use TLS before broader exposure. Container builds on LinuxONE use the native `s390x` Python image. Runtime dependencies avoid optional native extensions because the server build image has no compiler installed.

## Next integration step

Add a map interface that queries visible bounding boxes and lets the user select a gauge. Replace the trend calculation in `app/main.py` with the validated model artifact and feature pipeline. Keep its output separate from policy thresholds, then report chronological/event-level metrics and model provenance. Confirm timestamp spacing and units from the selected gauge source before relying on any forecast.
