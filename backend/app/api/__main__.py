"""``python -m app.api``: serve the API with every log line as JSON, from the first one.

Uvicorn's own logging setup is skipped (``log_config=None``), so its startup
and access lines go through the same structlog handler as the application.
"""

import os

import uvicorn

from app.logs import configure_logging

if __name__ == "__main__":
    configure_logging("api")
    uvicorn.run(
        "app.api.main:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8000")),
        log_config=None,
    )
