import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="Ask the real model instead of replaying recorded SQL")
    parser.addoption("--real-model", action="store_true", help="Run tests that load the local embedding model")
