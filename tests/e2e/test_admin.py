"""Admin UI end to end over HTTP: real Keycloak login form, real API, real Postgres sessions."""

import html
import re
import time
import uuid

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


@pytest.fixture
def second_browser():
    with httpx.Client(timeout=15) as client:
        yield client


def csrf_of(page: httpx.Response) -> str:
    match = CSRF.search(page.text)
    assert match, "no CSRF token on page"
    return match.group(1)


def submit(client: httpx.Client, url: str, page: httpx.Response, **fields: str) -> httpx.Response:
    """Post a form the way the browser would: with the page's CSRF token, following redirects."""
    return client.post(url, data={"csrf_token": csrf_of(page), **fields}, follow_redirects=True)


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


def test_support_handles_a_phishing_report_end_to_end(browser, second_browser, e2e_settings):
    """The brief's workflow as sam: find the link, judge its traffic, block it, see the record."""
    base = e2e_settings.admin_url
    # Setup: eddie owns a link, and a visitor follows it from webmail.
    form = keycloak_login(second_browser, base, "eddie", "/links/new")
    target = f"https://example.com/login-{uuid.uuid4().hex[:8]}"
    created = submit(second_browser, f"{base}/links", form, target_url=target)
    link_path = created.url.path
    short_url = html.unescape(re.search(r'data-copy="([^"]+)"', created.text).group(1))
    visit = httpx.get(short_url, headers={"Referer": "https://mail.google.com/mail/u/0/"})
    assert visit.status_code == 302 and visit.headers["location"] == target

    # 1. Find the link from the report: paste the short URL into search.
    keycloak_login(browser, base, "sam")
    found = browser.get(f"{base}/links", params={"q": short_url})
    assert found.status_code == 200 and f'href="{link_path}"' in found.text

    # 2. Judge its traffic: the click and its referrer show up on the link page.
    deadline = time.monotonic() + 30
    page = browser.get(f"{base}{link_path}")
    while "<strong>1</strong> clicks" not in page.text and time.monotonic() < deadline:
        time.sleep(0.5)  # the click-processor rolls the event up asynchronously
        page = browser.get(f"{base}{link_path}")
    assert "<strong>1</strong> clicks" in page.text and "mail.google.com" in page.text

    # 3. Disable it: block with a reason; visitors stop being redirected.
    blocked = submit(browser, f"{base}{link_path}/block", page, reason="Phishing report T-1")
    assert "Blocked by a moderator." in blocked.text
    assert httpx.get(short_url).status_code == 410

    # 4. A record of what happened and who did it.
    history = blocked.text[blocked.text.index("<h2>History</h2>") :]
    assert re.search(r"<td>Blocked</td>\s*<td>sam</td>\s*<td>Phishing report T-1</td>", history)

    # Clean up: unblocking needs a reason too, then the owner deletes the link.
    unblocked = submit(browser, f"{base}{link_path}/unblock", blocked, reason="Test done")
    assert re.search(r"<td>Unblocked</td>\s*<td>sam</td>\s*<td>Test done</td>", unblocked.text)
    owner_page = second_browser.get(f"{base}{link_path}")
    deleted = submit(second_browser, f"{base}{link_path}/delete", owner_page)
    assert "Link deleted" in deleted.text
