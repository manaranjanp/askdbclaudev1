from collections.abc import Callable
from pathlib import Path

import pytest
from _pytest.terminal import TerminalReporter

from tests.fixtures.make_sample_db import make_events_db, make_sample_db
from tests.golden.harness import GOLDEN_RESULTS, Case, mismatches, run_case


@pytest.fixture(scope="session")
def golden_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("golden") / "sample.sqlite"
    make_sample_db(path)
    make_events_db(path, rows=1500)
    return path


@pytest.fixture
def check_case(golden_db: Path, tmp_path: Path, request: pytest.FixtureRequest) -> Callable[[Case], list[str]]:
    live = bool(request.config.getoption("--live"))

    def check(case: Case) -> list[str]:
        answer, llm = run_case(case, golden_db, tmp_path / "app_store.sqlite", live=live)
        return mismatches(case, answer, llm)

    return check


def pytest_terminal_summary(
    terminalreporter: TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    rate = config.stash.get(GOLDEN_RESULTS, None)
    if rate is None:
        return
    passed, total = rate
    terminalreporter.write_sep("-", "golden suite")
    terminalreporter.write_line(f"pass rate: {passed}/{total} ({passed / total:.0%})")
