# AI Incident Manager — Dev Environment
# Full Python dev environment with uv, LangGraph, and MCP SDK
# Designed for Docker Desktop on Windows

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | bash
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

# Copy docs into container
COPY docs/ ./docs/

# Copy test script
COPY test-session.sh ./test-session.sh
RUN chmod +x ./test-session.sh

# Create project structure
RUN mkdir -p \
    backend/agent \
    backend/tiers \
    backend/skills \
    backend/mcp \
    backend/audit \
    cli \
    examples \
    logs

# Initialize Python project with uv
RUN uv init --no-workspace && \
    uv python pin 3.12

# Install core dependencies
RUN uv add \
    langgraph \
    langchain-anthropic \
    langchain-openai \
    langchain-ollama \
    ollama \
    mcp \
    fastapi \
    uvicorn \
    pydantic \
    pyyaml \
    typer \
    rich \
    python-dotenv \
    sqlalchemy[asyncio] \
    alembic \
    asyncpg \
    passlib[bcrypt] \
    python-jose[cryptography] \
    python-multipart

# Install dev dependencies
RUN uv add --dev \
    pytest \
    pytest-asyncio \
    ruff \
    mypy

# Copy example config and skill definition if they exist
COPY examples/ ./examples/ 2>/dev/null || true

# Copy .env.example into container
COPY .env.example .env.example

# On startup: check Ollama connectivity if provider is set to ollama,
# print session context, then drop into shell
CMD ["bash", "-c", "\
    echo '' && \
    echo '============================================================' && \
    echo '  Checking model provider...' && \
    echo '============================================================' && \
    PROVIDER=$(grep '^AIM_MODEL_PROVIDER' .env 2>/dev/null | cut -d= -f2 | tr -d ' ') && \
    MODEL=$(grep '^AIM_MODEL_ID' .env 2>/dev/null | cut -d= -f2 | tr -d ' ') && \
    OLLAMA_URL=$(grep '^OLLAMA_BASE_URL' .env 2>/dev/null | cut -d= -f2 | tr -d ' ') && \
    echo \"  Provider : ${PROVIDER:-not set}\" && \
    echo \"  Model    : ${MODEL:-not set}\" && \
    if [ \"$PROVIDER\" = \"ollama\" ]; then \
        echo '' && \
        echo '  Checking Ollama connection...' && \
        if curl -s --max-time 3 \"${OLLAMA_URL:-http://host.docker.internal:11434}/api/tags\" > /dev/null 2>&1; then \
            echo '  [OK] Ollama is reachable' && \
            AVAILABLE=$(curl -s \"${OLLAMA_URL:-http://host.docker.internal:11434}/api/tags\" | python3 -c \"import sys,json; models=json.load(sys.stdin).get('models',[]); [print('       -', m['name']) for m in models]\" 2>/dev/null) && \
            echo \"  Available models:\" && \
            echo \"$AVAILABLE\"; \
        else \
            echo '  [WARN] Ollama not reachable at '\"${OLLAMA_URL:-http://host.docker.internal:11434}\" && \
            echo '  Make sure Ollama is running on your Windows host.' && \
            echo '  Run: ollama serve'; \
        fi; \
    fi && \
    echo '============================================================' && \
    echo '' && \
    ./test-session.sh && exec bash"]