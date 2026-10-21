from collections.abc import Collection
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Checked anywhere in the tree, so a write hidden inside a CTE is still caught (SPEC 6.2).
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Pragma,
    exp.Attach,
    exp.Command,
)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None


def validate(sql: str, known_tables: Collection[str]) -> Verdict:
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as exc:
        return Verdict(False, f"Could not parse the SQL: {exc}")
    if len(statements) != 1 or statements[0] is None:
        return Verdict(False, "Exactly one statement is allowed.")
    statement = statements[0]
    if not isinstance(statement, (exp.Select, exp.Union)):
        return Verdict(False, "Only SELECT statements are allowed.")
    if statement.find(*_FORBIDDEN) is not None:
        return Verdict(False, "The statement contains a write or DDL keyword.")

    # A CTE name parses as a table reference, so it is not a database table.
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    known = {name.lower() for name in known_tables}
    for table in statement.find_all(exp.Table):
        name = table.name.lower()
        if name not in known and name not in cte_names:
            return Verdict(False, f"Unknown table: {table.name}")
    return Verdict(True)
