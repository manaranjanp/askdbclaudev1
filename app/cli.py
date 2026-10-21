import argparse
import getpass
import os
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.db.store import AppStore


def main() -> None:
    # Users are created by support staff on the machine, not through the web app (SPEC 3).
    parser = argparse.ArgumentParser(prog="app.cli")
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create-user", help="Create a login for an analyst")
    create.add_argument("username")
    args = parser.parse_args()

    store = AppStore(Path(os.environ.get("APP_STORE_PATH", "app_store.sqlite")))
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match.")
    if not password:
        raise SystemExit("Password must not be empty.")
    try:
        store.create_user(args.username, password)
    except IntegrityError:
        raise SystemExit(f"User already exists: {args.username}") from None
    print(f"Created user {args.username}.")


if __name__ == "__main__":
    main()
