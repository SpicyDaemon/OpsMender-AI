# OpsMender Frontend

This is the Next.js dashboard for the OpsMender AI.

**Important Note:** As of Sprint 13, OpsMender is a unified single-container application. We do not run the frontend and backend on separate ports in production or dev. The Python backend (FastAPI) automatically serves the statically exported Next.js frontend.

## Getting Started

Do **not** use `npm run dev` to start the web server on port 3000. 

To run the full stack (frontend + backend API endpoints), use the unified dev server from the project root:

```bash
cd ..
uv run python scripts/dev_server.py
```

This will start the backend on port `8000`, and it will serve both the API endpoints and the frontend dashboard at [http://localhost:8000](http://localhost:8000).

## Building the Frontend

When you make changes to the React code, you must rebuild the static export so the backend can serve the new files:

```bash
npm run build
```

This compiles the Next.js application into static HTML/JS/CSS inside the `frontend/out/` directory, which FastAPI then mounts and serves.
