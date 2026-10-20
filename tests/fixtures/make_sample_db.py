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


if __name__ == "__main__":
    make_sample_db(Path("tests/fixtures/sample.sqlite"))
