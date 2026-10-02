"""scripts/launchpad: generated local-UI launchpad (config from .env, live status badges)."""

import os
import socket
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/launchpad"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def healthy_port() -> Iterator[int]:
    """A tiny server answering 200 on every path, standing in for a running service."""

    class Ok(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield int(server.server_address[1])
    server.shutdown()
    server.server_close()


def run_launchpad(
    tmp_path: Path, env_lines: list[str], *, users_file: Path | None = None, **environ: str
) -> str:
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(env_lines) + "\n")
    output = tmp_path / "launchpad.html"
    env = {key: value for key, value in os.environ.items() if key != "KEYCLOAK_HOST_PORT"}
    users = ["--users-file", str(users_file)] if users_file else []
    result = subprocess.run(
        [str(SCRIPT), "--no-open", "--env-file", str(env_file), "--output", str(output), *users],
        capture_output=True,
        text=True,
        env={**env, **environ},
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output.read_text(encoding="utf-8")


def card(html: str, title: str) -> str:
    start = html.index(f'data-ui="{title}"')
    return html[start : html.index("</article>", start)]


def test_every_ui_has_a_card(tmp_path):
    html = run_launchpad(tmp_path, [f"KEYCLOAK_HOST_PORT={free_port()}"])
    for title in ("Admin UI", "API docs", "Grafana", "Keycloak console", "ElasticMQ stats"):
        assert f'data-ui="{title}"' in html, title
    assert 'href="http://localhost:8001"' in html
    assert 'href="http://localhost:8000/docs"' in html
    assert 'href="http://localhost:3000/d/shortener-overview"' in html
    assert 'target="_blank"' in html


def test_keycloak_link_follows_env_file_port(tmp_path):
    port = free_port()
    html = run_launchpad(tmp_path, [f"KEYCLOAK_HOST_PORT={port}"])
    assert f'href="http://localhost:{port}/admin/master/console/"' in card(html, "Keycloak console")


def test_process_environment_overrides_env_file(tmp_path):
    port = free_port()
    html = run_launchpad(tmp_path, ["KEYCLOAK_HOST_PORT=1"], KEYCLOAK_HOST_PORT=str(port))
    assert f"http://localhost:{port}/admin/master/console/" in html


def test_stopped_service_gets_not_running_badge_and_banner(tmp_path):
    html = run_launchpad(tmp_path, [f"KEYCLOAK_HOST_PORT={free_port()}"])
    assert "not running" in card(html, "Keycloak console")
    assert "make up" in html


def test_running_service_gets_running_badge(tmp_path, healthy_port):
    html = run_launchpad(tmp_path, [f"KEYCLOAK_HOST_PORT={healthy_port}"])
    keycloak = card(html, "Keycloak console")
    assert "● running" in keycloak
    assert "not running" not in keycloak


def test_no_secret_values_are_written(tmp_path):
    html = run_launchpad(
        tmp_path,
        [
            f"KEYCLOAK_HOST_PORT={free_port()}",
            "KEYCLOAK_ADMIN_USER=kcadmin",
            "KEYCLOAK_ADMIN_PASSWORD=kc-pass-9f8e7d",
            "ADMIN_DB_PASSWORD=db-pass-1a2b3c",
            "SHORTENER_ADMIN_CLIENT_SECRET=client-secret-4d5e6f",
            "ADMIN_COOKIE_SECRET=cookie-secret-7a8b9c",
        ],
    )
    assert "kcadmin" in card(html, "Keycloak console")
    for secret in (
        "kc-pass-9f8e7d",
        "db-pass-1a2b3c",
        "client-secret-4d5e6f",
        "cookie-secret-7a8b9c",
    ):
        assert secret not in html


def test_values_are_html_escaped(tmp_path):
    html = run_launchpad(
        tmp_path, [f"KEYCLOAK_HOST_PORT={free_port()}", "KEYCLOAK_ADMIN_USER=<b>x</b>"]
    )
    assert "<b>x</b>" not in html
    assert "<code>&lt;b&gt;x&lt;/b&gt;</code>" in card(html, "Keycloak console")


def test_sign_in_label_is_bold_and_actionable_values_are_code(tmp_path):
    html = run_launchpad(tmp_path, [f"KEYCLOAK_HOST_PORT={free_port()}", "KEYCLOAK_ADMIN_USER=kc"])
    admin = card(html, "Admin UI")
    assert "<strong>Sign in:</strong>" in admin
    for value in ("alice", "eddie", "erin", "victor", "nora", "password"):
        assert f"<code>{value}</code>" in admin, value
    assert "<code>make token USER=eddie</code>" in card(html, "API docs")
    keycloak = card(html, "Keycloak console")
    assert "<code>kc</code>" in keycloak
    assert "<code>KEYCLOAK_ADMIN_PASSWORD</code>" in keycloak
    assert "<code>.env</code>" in keycloak


def test_keycloak_card_calls_out_the_realm(tmp_path):
    html = run_launchpad(tmp_path, [f"KEYCLOAK_HOST_PORT={free_port()}"])
    assert "<code>shortener</code>" in card(html, "Keycloak console")


USERS_YAML = """
users:
  - username: ada
    password: pw-ada
    roles: [admin]
  - username: ed
    password: pw-ed
    roles: [editor, viewer]
  - username: nobody
    password: pw-nobody
    roles: []
"""


def test_admin_card_has_a_user_table_from_users_yaml(tmp_path):
    users_file = tmp_path / "users.yaml"
    users_file.write_text(USERS_YAML)
    admin = card(
        run_launchpad(tmp_path, [f"KEYCLOAK_HOST_PORT={free_port()}"], users_file=users_file),
        "Admin UI",
    )
    assert "<th>Username</th><th>Role</th><th>Password</th>" in admin
    assert "<tr><td><code>ada</code></td><td>admin</td><td><code>pw-ada</code></td></tr>" in admin
    assert "<td><code>ed</code></td><td>editor, viewer</td>" in admin
    assert "<td><code>nobody</code></td><td>(no access)</td>" in admin


def test_admin_card_falls_back_when_users_yaml_is_missing(tmp_path):
    admin = card(
        run_launchpad(
            tmp_path, [f"KEYCLOAK_HOST_PORT={free_port()}"], users_file=tmp_path / "missing.yaml"
        ),
        "Admin UI",
    )
    assert "<table" not in admin
    assert "<code>infra/keycloak/users.yaml</code>" in admin
