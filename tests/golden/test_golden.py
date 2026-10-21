from collections.abc import Callable
from dataclasses import replace

import pytest

from tests.golden.harness import GATE, GATE_MIN_CASES, GOLDEN_RESULTS, Case, load_cases

CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_case(case: Case, check_case: Callable[[Case], list[str]]) -> None:
    assert check_case(case) == []


def test_release_gate(
    request: pytest.FixtureRequest, check_case: Callable[[Case], list[str]]
) -> None:
    passed = sum(1 for case in CASES if not check_case(case))
    total = len(CASES)
    request.config.stash[GOLDEN_RESULTS] = (passed, total)
    if total >= GATE_MIN_CASES:
        assert passed / total >= GATE


def test_changed_expected_result_fails(check_case: Callable[[Case], list[str]]) -> None:
    case = CASES[0]
    wrong = replace(case, expect={**case.expect, "rows": [["Nowhere", 0.0]]})
    assert check_case(wrong) != []
