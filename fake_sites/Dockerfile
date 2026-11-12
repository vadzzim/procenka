FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY fake_sites ./fake_sites
RUN uv sync --frozen --no-dev --no-editable

RUN useradd --system --uid 1000 app
USER app

EXPOSE 8001
CMD ["uvicorn", "fake_sites.app:app", "--host", "0.0.0.0", "--port", "8001"]
