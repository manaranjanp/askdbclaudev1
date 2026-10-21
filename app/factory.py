from pathlib import Path

from fastapi import FastAPI

from app.api.routes import router
from app.config.loader import load_databases, load_limits
from app.db.analytics import open_readonly
from app.db.store import AppStore
from app.llm.client import LLMClient
from app.pipeline.run import Pipeline


def create_app(config_dir: Path, store_path: Path, llm: LLMClient) -> FastAPI:
    databases = load_databases(config_dir)
    engines = {name: open_readonly(cfg.path) for name, cfg in databases.items()}
    app = FastAPI()
    app.state.pipeline = Pipeline(engines, llm, AppStore(store_path), load_limits(config_dir))
    app.include_router(router)
    return app
