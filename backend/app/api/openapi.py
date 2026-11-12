"""The committed OpenAPI schema ``backend/openapi.json`` (TypeScript types are generated from it).

``uv run --directory backend python -m app.api.openapi`` writes it;
``... --check`` exits with 1 if the file differs from the application.
"""

import json
import sys
from pathlib import Path
from typing import Any

from app.api.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def current_schema() -> dict[str, Any]:
    return create_app().openapi()


def render(schema: dict[str, Any]) -> str:
    """Formatted and deterministic: keys keep FastAPI's order, LF line endings."""
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str]) -> int:
    text = render(current_schema())
    if "--check" in argv:
        if not OPENAPI_PATH.exists() or OPENAPI_PATH.read_text(encoding="utf-8") != text:
            print(f"{OPENAPI_PATH.name} is out of date: run python -m app.api.openapi")
            return 1
        print(f"{OPENAPI_PATH.name} is up to date")
        return 0
    OPENAPI_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"written {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
