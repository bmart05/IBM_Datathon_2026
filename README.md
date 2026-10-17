# IBM Datathon 2026

## Agent communication hub

**Agents: start at [coordination/README.md](coordination/README.md).**

This is the `codex/communication` branch. Read each other's updates and publish only your own agent file under `coordination/agents/`. Agent 1's data handoff and requests to Agents 2–5 are already posted. The other agents must acknowledge in their own files before we can claim they are connected.

This branch carries coordination documents only. Its application files are a snapshot, not the current integrated application. Continue development on your assigned code branch; do not merge this communication branch into code branches or deploy it.

Application repository for the IBM Datathon project. Components live in separate top-level folders so the API, user interface, and shared deployment configuration can evolve independently.

## Components

- [`backend/`](backend/README.md): FastAPI service, NOAA U.S. gauge integration, and LinuxONE container deployment.
- [`frontend/`](frontend/README.md): dependency-free historical flood replay dashboard.

## Backend quick start

```sh
cd backend
podman build -t riverguard-api:latest .
podman run --rm -p 8000:8000 riverguard-api:latest
```

Open `http://localhost:8000/docs` for the API reference. See the [backend README](backend/README.md) for station endpoints and LinuxONE deployment details.

## Replay dashboard

Run the backend on port 8001 (or set a different API URL in the dashboard), then start the frontend:

```sh
cd frontend
python3 -m http.server 5173
```

Open `http://localhost:5173` to replay historical events and compare baseline metrics. See the [frontend README](frontend/README.md) for API connection details and the scope of the illustrative trend view.
