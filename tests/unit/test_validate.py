import pytest

from app.pipeline.validate import validate

KNOWN = {"sales", "regions"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, SUM(amount) FROM sales GROUP BY region",
        "SELECT r.region FROM sales s JOIN regions r ON r.id = s.region_id",
        "WITH t AS (SELECT region, amount FROM sales) SELECT * FROM t",
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 5) SELECT x FROM c",
        "SELECT 1 FROM sales /* trailing comment; with a semicolon */",
        "SELECT region FROM sales UNION SELECT region FROM sales",
    ],
)
def test_valid_selects_pass(sql: str) -> None:
    assert validate(sql, KNOWN).ok


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE sales",
        "DELETE FROM sales",
        "SELECT 1; DROP TABLE sales",
        "SELECT 1 -- harmless\n; DELETE FROM sales",
        "/* hide it */ DROP TABLE sales",
        "WITH d AS (DELETE FROM sales RETURNING *) SELECT * FROM d",
        "WITH x AS (SELECT 1) INSERT INTO sales SELECT * FROM x",
        "PRAGMA table_info(sales)",
        "ATTACH DATABASE 'other.db' AS other",
        "UPDATE sales SET amount = 0",
    ],
)
def test_writes_and_ddl_are_rejected(sql: str) -> None:
    verdict = validate(sql, KNOWN)
    assert not verdict.ok
    assert verdict.reason


def test_unknown_table_is_rejected_with_its_name() -> None:
    verdict = validate("SELECT * FROM secret_salaries", KNOWN)
    assert not verdict.ok
    assert verdict.reason == "Unknown table: secret_salaries"


def test_table_name_match_ignores_case() -> None:
    assert validate("SELECT * FROM SALES", KNOWN).ok


def test_unknown_table_inside_cte_is_rejected() -> None:
    assert not validate("WITH t AS (SELECT * FROM nowhere) SELECT * FROM t", KNOWN).ok
