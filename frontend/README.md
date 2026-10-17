# RiverGuard frontend

Static, dependency-free replay dashboard. It lives beside `backend/` as an independent app component and consumes the backend replay API.

## Run locally

Start the backend on port 8001 (for example, through the LinuxONE SSH tunnel), then serve this directory:

```sh
cd frontend
python3 -m http.server 5173
```

Open `http://localhost:5173`. The default API URL is `http://localhost:8001`; change **API base URL** in the page if the service is elsewhere. You can also pass `?api=https://your-api.example` in the page URL. The backend must allow the frontend origin in its `CORS_ORIGINS` environment variable; local ports 5173 and 5500 are allowed by default.

## Replay view

The dashboard reads `GET /v1/replays`, the selected `GET /v1/replays/{replay_id}` series, and `GET /v1/replays/{replay_id}/evaluation`. At each replay position, it sends only observations up to that timestamp to `POST /v1/predict` for cautious, balanced, and early policy decisions. The timeline shows recorded stage against the published minor flood threshold. The point-in-time trend panel fits a simple line to the previous hour and projects six hours forward; it is an illustrative replay calculation, not a calibrated or operational forecast. The policy score is an uncalibrated prototype heuristic, and every alert is labelled for human review. The event-level panel reports the backend's persistence-versus-trend metrics and data-gap exclusions; those retrospective metrics are not altered by the live policy comparison.
