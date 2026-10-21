import sqlite3
from pathlib import Path


def make_sample_db(path: Path) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT NOT NULL, amount REAL NOT NULL)")
        conn.executemany(
            "INSERT INTO sales (region, amount) VALUES (?, ?)",
            [("North", 120.0), ("North", 80.0), ("South", 200.0), ("West", 50.0)],
        )
        conn.commit()
    finally:
        conn.close()


def make_events_db(path: Path, rows: int) -> None:
    # Adds a large table to an existing database, for row-cap and timeout tests.
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO events (id) VALUES (?)", [(i,) for i in range(1, rows + 1)])
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    make_sample_db(Path("tests/fixtures/sample.sqlite"))
