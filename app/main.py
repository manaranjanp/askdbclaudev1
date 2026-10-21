import logging
import os
from pathlib import Path

from app.config.loader import load_llm_model
from app.factory import create_app
from app.llm.anthropic_client import AnthropicLLM

# Startup warnings (excluded columns, unavailable databases) are printed with their file and reason.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

config_dir = Path(os.environ.get("APP_CONFIG_DIR", "config"))
app = create_app(
    config_dir=config_dir,
    store_path=Path(os.environ.get("APP_STORE_PATH", "app_store.sqlite")),
    llm=AnthropicLLM(model=load_llm_model(config_dir)),
)
