"""Step 7: server-rendered pages (shell, config, auth) and static assets."""

import json
import pathlib
import re

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

from document_intake.tests.factories import DocumentFactory

pytestmark = pytest.mark.django_db

LIST = reverse("document_intake:document-list")


@pytest.fixture
def ui_client(client, user):
    client.force_login(user)
    return client


def page_config(response) -> dict:
    match = re.search(
        rb'<script id="intake-config" type="application/json">(.*?)</script>', response.content
    )
    assert match, "config script missing"
    return json.loads(match.group(1))


def test_list_page_shell(ui_client) -> None:
    response = ui_client.get(LIST)
    assert response.status_code == 200
    html = response.content.decode()
    assert '<meta name="csrf-token" content="' in html
    assert 'data-api-base="/api/v1/intake/"' in html
    assert 'aria-current="page"' in html
    assert 'type="module"' in html
    assert "document_intake/js/list.js" in html
    assert 'capture="environment"' in html
    assert "JPEG, JPG, PDF, PNG" in html
    assert 'action="/accounts/logout/"' in html


def test_list_page_config(ui_client) -> None:
    cfg = page_config(ui_client.get(LIST))
    assert cfg["statuses"]["needs_review"] == "Needs review"
    assert cfg["docTypes"]["auto"] == "Auto-detect"
    assert cfg["allowedExtensions"] == [".jpeg", ".jpg", ".pdf", ".png"]
    assert cfg["maxUploadBytes"] == 25 * 1024 * 1024
    assert cfg["reviewUrlTemplate"].startswith("/intake/documents/")
    assert cfg["fileUrlTemplate"].endswith("/file/")
    assert cfg["pageSize"] == 25


def test_config_script_cannot_break_out(ui_client, user) -> None:
    # json_script escapes "<", so a hostile value cannot close the script tag.
    html = ui_client.get(LIST).content.decode()
    config_block = html.split('id="intake-config"', 1)[1].split("</script>", 1)[0]
    assert "<" not in config_block.split(">", 1)[1]


def test_pages_require_login(client) -> None:
    response = client.get(LIST)
    assert response.status_code == 302
    assert response["Location"] == f"/accounts/login/?next={LIST}"


def test_login_flow(client, user) -> None:
    page = client.get("/accounts/login/")
    assert page.status_code == 200
    bad = client.post("/accounts/login/", {"username": user.username, "password": "wrong"})
    assert b"not correct" in bad.content
    good = client.post(
        "/accounts/login/", {"username": user.username, "password": "pass-for-tests"}
    )
    assert good.status_code == 302
    assert good["Location"] == LIST


def test_logout_requires_post(ui_client) -> None:
    assert ui_client.get("/accounts/logout/").status_code == 405
    assert ui_client.post("/accounts/logout/").status_code == 302


def test_review_page_for_own_document(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, original_filename="<b>td</b>.pdf")
    response = ui_client.get(reverse("document_intake:document-review", args=[doc.pk]))
    assert response.status_code == 200
    assert b"&lt;b&gt;td&lt;/b&gt;.pdf" in response.content  # auto-escaped


def test_review_page_for_other_users_document_is_404(ui_client, other_user) -> None:
    doc = DocumentFactory(uploaded_by=other_user)
    response = ui_client.get(reverse("document_intake:document-review", args=[doc.pk]))
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "document_intake/css/tokens.css",
        "document_intake/css/base.css",
        "document_intake/css/layout.css",
        "document_intake/css/components.css",
        "document_intake/js/api.js",
        "document_intake/js/dom.js",
        "document_intake/js/format.js",
        "document_intake/js/list.js",
        "document_intake/js/table.js",
        "document_intake/js/toast.js",
        "document_intake/js/upload.js",
    ],
)
def test_static_assets_exist(path) -> None:
    assert finders.find(path), path


def test_no_external_requests_in_assets() -> None:
    """No CDNs, web fonts or remote URLs: everything is served by us."""
    for path in [
        f"document_intake/css/{n}.css" for n in ("tokens", "base", "layout", "components")
    ]:
        content = pathlib.Path(finders.find(path)).read_text(encoding="utf-8")
        for needle in ("http://", "https://", "@import", "url("):
            assert needle not in content, (path, needle)


def test_js_never_uses_innerhtml() -> None:
    js_dir = pathlib.Path(finders.find("document_intake/js/dom.js")).parent
    for js in js_dir.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert "innerHTML" not in text, js.name
        assert "insertAdjacentHTML" not in text, js.name
        assert "document.write" not in text, js.name
