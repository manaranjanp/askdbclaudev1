import logging
from pathlib import Path

from fastapi import FastAPI

from app.api.routes import router
from app.config.catalog_check import Catalog, check_catalog
from app.config.examples import load_examples
from app.config.loader import load_limits, load_retrieval
from app.db.analytics import table_names
from app.db.store import AppStore
from app.llm.client import LLMClient
from app.pipeline.run import Pipeline
from app.retrieval.embedder import Embedder
from app.retrieval.index import DeclaredContext, Retrieval, SchemaIndex

logger = logging.getLogger(__name__)


def create_app(config_dir: Path, store_path: Path, llm: LLMClient, embedder: Embedder | None = None) -> FastAPI:
    catalog = check_catalog(config_dir)
    store = AppStore(store_path)
    retrieval = _build_retrieval(config_dir, catalog, embedder)
    app = FastAPI()
    app.state.databases = catalog.statuses
    app.state.store = store
    app.state.pipeline = Pipeline(
        catalog.engines,
        llm,
        store,
        load_limits(config_dir),
        catalog.unavailable,
        retrieval,
    )
    app.include_router(router)
    return app


def _build_retrieval(config_dir: Path, catalog: Catalog, embedder: Embedder | None) -> Retrieval | None:
    if embedder is None:
        # Without an embedder the whole schema goes to the model. Production wiring waits on SPEC Q10.
        logger.warning("retrieval disabled: no embedder configured, the full schema is sent")
        return None
    indexes = {}
    for name, engine in catalog.engines.items():
        # Examples are checked against the database here, so a bad one is skipped and logged at startup.
        examples = load_examples(config_dir / "examples", name, table_names(engine))
        declared = DeclaredContext(
            tables=catalog.tables[name],
            metrics=catalog.metrics[name],
            notes=catalog.notes[name],
            joins=catalog.joins[name],
        )
        indexes[name] = SchemaIndex.build(engine, declared, examples, embedder)
    return Retrieval(indexes=indexes, settings=load_retrieval(config_dir))
