import logging
import os
from pathlib import Path

from app.factory import create_app
from app.llm.client import MockLLM

# Startup warnings (excluded columns, unavailable databases) are printed with their file and reason.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Placeholder until the Anthropic client arrives in slice 6.
app = create_app(
    config_dir=Path(os.environ.get("APP_CONFIG_DIR", "config")),
    store_path=Path(os.environ.get("APP_STORE_PATH", "app_store.sqlite")),
    llm=MockLLM("SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"),
)
