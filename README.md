# IBM Datathon 2026

Application repository for the IBM Datathon project. Components live in separate top-level folders so the API, user interface, and shared deployment configuration can evolve independently.

## Components

- [`backend/`](backend/README.md): FastAPI service, NOAA U.S. gauge integration, and LinuxONE container deployment.
- `frontend/`: reserved for the application interface.

## Backend quick start

```sh
cd backend
podman build -t riverguard-api:latest .
podman run --rm -p 8000:8000 riverguard-api:latest
```

Open `http://localhost:8000/docs` for the API reference. See the [backend README](backend/README.md) for station endpoints and LinuxONE deployment details.
