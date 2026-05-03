# Docker Dev Environment

## First Time Setup

```bash
# Build the container
docker compose build

# Start and enter the container
docker compose run --rm aim-dev
```

On startup the container will automatically print your session context (PROMPT_CONTEXT + CURRENT_STATE + TASKS) and drop you into a bash shell.

---

## Daily Usage

```bash
# Start dev environment
docker compose run --rm aim-dev

# Inside the container — run tests
uv run pytest

# Inside the container — lint
uv run ruff check .

# Inside the container — manually run session script
./test-session.sh
```

---

## Adding Dependencies

Inside the container:
```bash
uv add <package>
```

This updates `pyproject.toml` and `uv.lock`. Since the project folder is mounted as a volume, changes sync back to your Windows machine automatically.

---

## Rebuilding

Only needed if you change the Dockerfile:
```bash
docker compose build --no-cache
```

---

## File Sync

The entire project folder is mounted into the container at `/app`. This means:
- Edit files in VS Code on Windows → changes appear instantly inside the container
- Create files inside the container → they appear on Windows immediately
- No need to rebuild the image when you change code

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your API keys:
```bash
cp .env.example .env
```

Then update `docker-compose.yml` to use `.env` instead of `.env.example`.

---

## Production-like Deployment (Single Container)

For a deployment that mirrors production (Frontend + Backend + Database), use the configurations in the `docker/` directory.

```bash
# Start the full stack (App + Postgres)
docker compose -f docker/docker-compose.yml up -d

# Run migrations (if not automatic)
docker compose -f docker/docker-compose.yml exec app python -m alembic upgrade head
```

This setup:
1. Builds a single-container image for the App (Next.js static export served by FastAPI).
2. Sets up a persistent PostgreSQL database.
3. Automatically applies database migrations on startup.
4. Uses environment variables from the root `.env` file.
