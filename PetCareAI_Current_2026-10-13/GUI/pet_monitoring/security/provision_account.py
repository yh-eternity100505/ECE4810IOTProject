"""Local account setup. Prompts hide passwords; only salted hashes are stored."""
import argparse
import getpass
from pathlib import Path
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.services.authentication_service import AuthenticationService


def main():
    parser = argparse.ArgumentParser(description="Provision the local PetCare login account")
    parser.add_argument("--database", type=Path, default=Path(__file__).resolve().parents[2] / "data" / "pet_monitor.db")
    parser.add_argument("--username", help="New username (password is prompted securely)")
    args = parser.parse_args()
    username = args.username or input("New username: ").strip()
    password = getpass.getpass("New password (at least 6 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords did not match. Account unchanged.")
        return 1
    db = DatabaseService(args.database)
    try:
        if not db.available:
            print("Database unavailable. Account unchanged.")
            return 1
        auth = AuthenticationService(db)
        try:
            auth.set_credentials(username, password)
        except ValueError as error:
            print(str(error))
            return 1
        if not db.available:
            print("Account could not be saved. Check database access.")
            return 1
        print("Local account saved. Restart PetCare AI to use it.")
        return 0
    finally:
        password = confirmation = ""
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
