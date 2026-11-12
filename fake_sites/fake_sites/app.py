"""The fake-sites application: synthetic competitor sources of the demo.

Run: ``uvicorn fake_sites.app:app --port 8001``. Failure modes and the JSON
API arrive in later steps.
"""

from typing import Annotated

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from fake_sites.catalog import Site, search
from fake_sites.html_catalog import render_search_page


def create_app() -> FastAPI:
    application = FastAPI(title="procenka fake-sites", description="Синтетические площадки демо")

    @application.get("/html/search", response_class=HTMLResponse)
    async def html_search(article: Annotated[str, Query(max_length=100)] = "") -> str:
        return render_search_page(article, search(Site.HTML, article))

    return application


app = create_app()
