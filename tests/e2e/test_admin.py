"""Admin UI end to end over HTTP: real Keycloak login form, real API, real Postgres sessions."""

import html
import re

import httpx
import pytest

pytestmark = pytest.mark.e2e
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def keycloak_login(
    client: httpx.Client, admin_url: str, username: str, next_path: str = "/"
) -> httpx.Response:
    """Follow /auth/login to Keycloak, submit its login form, and land back on the admin UI."""
    page = client.get(f"{admin_url}/auth/login", params={"next": next_path}, follow_redirects=True)
    # Keycloak sets Secure cookies even over http; browsers allow that on localhost, httpx does not.
    for cookie in client.cookies.jar:
        cookie.secure = False
    form_tag = re.search(r'<form[^>]*\bid="kc-form-login"[^>]*>', page.text)
    assert form_tag, f"Keycloak login form not found (status {page.status_code})"
    action = html.unescape(re.search(r'action="([^"]+)"', form_tag.group(0)).group(1))
    return client.post(
        action, data={"username": username, "password": "password"}, follow_redirects=True
    )


@pytest.fixture
def browser():
    with httpx.Client(timeout=15) as client:
        yield client


def csrf_of(page: httpx.Response) -> str:
    match = CSRF.search(page.text)
    assert match, "no CSRF token on page"
    return match.group(1)


def test_eddie_logs_in_and_lands_on_next(browser, e2e_settings):
    landed = keycloak_login(browser, e2e_settings.admin_url, "eddie", "/links")
    assert landed.status_code == 200
    assert str(landed.url) == f"{e2e_settings.admin_url}/links"
    assert "eddie" in landed.text and "Sign out" in landed.text


def test_editor_creates_and_deletes_a_link_through_the_ui(browser, e2e_settings):
    base = e2e_settings.admin_url
    form = keycloak_login(browser, base, "eddie", "/links/new")
    created = browser.post(f"{base}/links", data={"csrf_token": csrf_of(form), "target_url": "https://example.com/ui"},
                           follow_redirects=True)  # fmt: skip
    assert created.status_code == 200 and "Short link created" in created.text
    link_path = created.url.path
    deleted = browser.post(
        f"{base}{link_path}/delete", data={"csrf_token": csrf_of(created)}, follow_redirects=True
    )
    assert "Link deleted" in deleted.text


def test_post_without_csrf_is_rejected(browser, e2e_settings):
    keycloak_login(browser, e2e_settings.admin_url, "eddie")
    response = browser.post(
        f"{e2e_settings.admin_url}/links", data={"target_url": "https://example.com/x"}
    )
    assert response.status_code == 403


def test_user_without_roles_sees_no_access(browser, e2e_settings):
    landed = keycloak_login(browser, e2e_settings.admin_url, "nora")
    assert landed.status_code == 403 and "don't have access" in landed.text


def test_viewer_cannot_create(browser, e2e_settings):
    keycloak_login(browser, e2e_settings.admin_url, "victor")
    assert browser.get(f"{e2e_settings.admin_url}/links/new").status_code == 403


def test_logout_ends_the_session(browser, e2e_settings):
    base = e2e_settings.admin_url
    home = keycloak_login(browser, base, "eddie")
    out = browser.post(f"{base}/auth/logout", data={"csrf_token": csrf_of(home)})
    assert out.status_code == 303 and "/protocol/openid-connect/logout" in out.headers["location"]
    after = browser.get(f"{base}/")
    assert after.status_code == 303 and after.headers["location"].startswith("/auth/login")
