from __future__ import annotations

from app.hash_password import main


def test_hash_password_prints_compose_ready_line(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.hash_password.getpass.getpass", lambda _prompt: "secret")
    main()
    output = capsys.readouterr().out
    assert "$argon2id$" in output
    assert "VENUE_INVENTORY_ADMIN_PASSWORD_HASH=$$argon2id$$" in output
    assert "each $ is doubled" in output
    assert "Generated administrator password" not in output
