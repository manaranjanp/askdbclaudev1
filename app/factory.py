from pathlib import Path

from fastapi import FastAPI

from app.api.routes import router
from app.config.catalog_check import check_catalog
from app.config.loader import load_limits
from app.db.store import AppStore
from app.llm.client import LLMClient
from app.pipeline.run import Pipeline


def create_app(config_dir: Path, store_path: Path, llm: LLMClient) -> FastAPI:
    catalog = check_catalog(config_dir)
    app = FastAPI()
    app.state.databases = catalog.statuses
    app.state.pipeline = Pipeline(
        catalog.engines,
        llm,
        AppStore(store_path),
        load_limits(config_dir),
        catalog.unavailable,
    )
    app.include_router(router)
    return app
