from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys

from argon2 import PasswordHasher

PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
PASSWORD_LENGTH = 20


def generate_admin_password(length: int = PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def generate_secret(nbytes: int = 48) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_password(password: str) -> str:
    return PasswordHasher().hash(password)


def compose_ready_hash_line(encoded: str) -> str:
    return "VENUE_INVENTORY_ADMIN_PASSWORD_HASH=" + encoded.replace("$", "$$")


def render_prod_env(
    *,
    secret_key: str,
    access_code_hmac_secret: str,
    bind_address: str = "0.0.0.0",
) -> str:
    return "\n".join(
        [
            f"VENUE_INVENTORY_SECRET_KEY={secret_key}",
            f"VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET={access_code_hmac_secret}",
            "VENUE_INVENTORY_SESSION_COOKIE_SECURE=true",
            "VENUE_INVENTORY_TRUST_PROXY=true",
            "VENUE_INVENTORY_REQUIRE_DATA_MOUNT=true",
            "VENUE_INVENTORY_LOG_LEVEL=INFO",
            "VENUE_INVENTORY_PORT=8080",
            f"VENUE_INVENTORY_BIND_ADDRESS={bind_address}",
            "",
        ]
    )


def _print_hash_result(password: str, encoded: str) -> None:
    print(encoded)
    print()
    print("Compose-ready .env line (each $ is doubled):")
    print(compose_ready_hash_line(encoded))
    if password:
        print()
        print("Generated administrator password (shown once):")
        print(password)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Hash or generate the Venue Inventory administrator password."
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate a random administrator password and print its hash.",
    )
    parser.add_argument(
        "--bootstrap-json",
        action="store_true",
        help="Print a JSON object with a one-time password, hash, and prod env.",
    )
    parser.add_argument(
        "--bind-address",
        default="0.0.0.0",
        help="Initial published bind address written into the prod env file.",
    )
    args = parser.parse_args([] if argv is None else argv)

    if args.bootstrap_json:
        password = generate_admin_password()
        encoded = hash_password(password)
        payload = {
            "password": password,
            "hash": encoded,
            "env": render_prod_env(
                secret_key=generate_secret(),
                access_code_hmac_secret=generate_secret(),
                bind_address=args.bind_address,
            ),
        }
        print(json.dumps(payload))
        return

    if args.generate:
        password = generate_admin_password()
        encoded = hash_password(password)
        _print_hash_result(password, encoded)
        return

    password = getpass.getpass("Administrator password: ")
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        raise SystemExit(1)
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)
    encoded = hash_password(password)
    _print_hash_result("", encoded)


if __name__ == "__main__":
    main(sys.argv[1:])
