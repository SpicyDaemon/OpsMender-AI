# AI Incident Manager

This repository contains the initial scaffolding for the AI Incident Manager
project.  It includes:

* A minimal Python project layout using `uv`/`poetry`.
* A simple configuration loader (`backend/config_loader.py`).
* A basic CLI entry point (`cli/aim.py`) that loads the configuration and
  prints it.
* A placeholder `config.yaml` with example settings.

## Getting Started

```bash
uv sync          # Install dependencies
uv run aim        # Run the CLI
```

Feel free to extend the CLI with additional sub‑commands as the project
progresses.
