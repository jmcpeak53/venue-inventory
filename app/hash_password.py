from __future__ import annotations

import getpass
import sys

from argon2 import PasswordHasher


def main() -> None:
    password = getpass.getpass("Administrator password: ")
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        raise SystemExit(1)
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)
    encoded = PasswordHasher().hash(password)
    print(encoded)
    print()
    print("Compose-ready .env line (each $ is doubled):")
    print("VENUE_INVENTORY_ADMIN_PASSWORD_HASH=" + encoded.replace("$", "$$"))


if __name__ == "__main__":
    main()
