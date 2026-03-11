import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from src import google_oauth_api as oauth


class _DummyResponse:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload


def _valid_credentials():
    return oauth.Credentials(
        access_token="token",
        refresh_token="",
        client_id="",
        client_secret="",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_extract_project_id_from_resource_name_supports_terminal_projects_path():
    assert (
        oauth._extract_project_id_from_resource_name(
            "projects/demo-project/locations/global"
        )
        == "demo-project"
    )
    assert (
        oauth._extract_project_id_from_resource_name("projects/demo-project")
        == "demo-project"
    )


def test_select_default_project_supports_v3_name_only_payload():
    projects = [
        {
            "name": "projects/my-project",
            "displayName": "My Project",
            "state": "ACTIVE",
        }
    ]

    selected = asyncio.run(oauth.select_default_project(projects))
    assert selected == "my-project"


def test_get_user_projects_fallbacks_to_v1_when_v3_search_fails(monkeypatch):
    called_urls = []

    async def fake_get_resource_manager_api_url():
        return "https://cloudresourcemanager.googleapis.com"

    async def fake_get_proxy_config():
        return None

    async def fake_get_async(url, headers=None, **kwargs):
        called_urls.append(url)
        if url.endswith("/v3/projects:search"):
            return _DummyResponse(403, {"error": "forbidden"}, "forbidden")
        if url.endswith("/v1/projects"):
            return _DummyResponse(
                200,
                {
                    "projects": [
                        {
                            "projectId": "proj-a",
                            "displayName": "Project A",
                            "lifecycleState": "ACTIVE",
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(
        oauth, "get_resource_manager_api_url", fake_get_resource_manager_api_url
    )
    monkeypatch.setattr(oauth, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(oauth, "get_async", fake_get_async)

    projects = asyncio.run(oauth.get_user_projects(_valid_credentials()))

    assert len(projects) == 1
    assert projects[0]["projectId"] == "proj-a"
    assert called_urls == [
        "https://cloudresourcemanager.googleapis.com/v3/projects:search",
        "https://cloudresourcemanager.googleapis.com/v1/projects",
    ]


def test_get_user_projects_retries_http11_after_connecterror(monkeypatch):
    calls = []

    async def fake_get_resource_manager_api_url():
        return "https://cloudresourcemanager.googleapis.com"

    async def fake_get_proxy_config():
        return "http://127.0.0.1:7890"

    async def fake_get_async(url, headers=None, **kwargs):
        http2 = kwargs.get("http2", True)
        calls.append((url, http2))

        if url.endswith("/v3/projects:search") and http2:
            raise httpx.ConnectError("h2 connect failed")

        if url.endswith("/v3/projects:search") and not http2:
            return _DummyResponse(
                200,
                {
                    "projects": [
                        {
                            "projectId": "proj-http11",
                            "displayName": "HTTP11 Fallback",
                            "state": "ACTIVE",
                        }
                    ]
                },
            )

        raise AssertionError(f"Unexpected URL: {url}, http2={http2}")

    monkeypatch.setattr(
        oauth, "get_resource_manager_api_url", fake_get_resource_manager_api_url
    )
    monkeypatch.setattr(oauth, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(oauth, "get_async", fake_get_async)

    projects, diagnostics = asyncio.run(
        oauth.get_user_projects(_valid_credentials(), with_diagnostics=True)
    )

    assert len(projects) == 1
    assert projects[0]["projectId"] == "proj-http11"
    assert diagnostics["connect_error_count"] == 1
    assert diagnostics["total_attempts"] == 2
    assert diagnostics["all_failed_by_connect_error"] is False
    assert diagnostics["proxy_configured"] is True
    assert calls == [
        ("https://cloudresourcemanager.googleapis.com/v3/projects:search", True),
        ("https://cloudresourcemanager.googleapis.com/v3/projects:search", False),
    ]


def test_get_user_projects_iterates_to_googleapis_fallback_after_connecterrors(
    monkeypatch,
):
    calls = []

    async def fake_get_resource_manager_api_url():
        return "https://cloudresourcemanager.googleapis.com"

    async def fake_get_proxy_config():
        return None

    async def fake_get_async(url, headers=None, **kwargs):
        http2 = kwargs.get("http2", True)
        calls.append((url, http2))

        # 第三个端点成功
        if url == "https://www.googleapis.com/cloudresourcemanager/v1/projects":
            return _DummyResponse(
                200,
                {
                    "projects": [
                        {
                            "projectId": "proj-googleapis",
                            "displayName": "Google APIs Fallback",
                            "lifecycleState": "ACTIVE",
                        }
                    ]
                },
            )

        # 前两个端点都发生连接错误（含HTTP/1.1回退）
        if url.endswith("/v3/projects:search"):
            raise httpx.ConnectError("v3 connect failed")

        if url.startswith(
            "https://cloudresourcemanager.googleapis.com/"
        ) and url.endswith("/v1/projects"):
            raise httpx.ConnectError("v1 connect failed")

        raise AssertionError(f"Unexpected URL: {url}, http2={http2}")

    monkeypatch.setattr(
        oauth, "get_resource_manager_api_url", fake_get_resource_manager_api_url
    )
    monkeypatch.setattr(oauth, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(oauth, "get_async", fake_get_async)

    projects, diagnostics = asyncio.run(
        oauth.get_user_projects(_valid_credentials(), with_diagnostics=True)
    )

    assert len(projects) == 1
    assert projects[0]["projectId"] == "proj-googleapis"
    assert diagnostics["connect_error_count"] == 4
    assert diagnostics["total_attempts"] == 5
    assert diagnostics["all_failed_by_connect_error"] is False
    assert calls == [
        ("https://cloudresourcemanager.googleapis.com/v3/projects:search", True),
        ("https://cloudresourcemanager.googleapis.com/v3/projects:search", False),
        ("https://cloudresourcemanager.googleapis.com/v1/projects", True),
        ("https://cloudresourcemanager.googleapis.com/v1/projects", False),
        ("https://www.googleapis.com/cloudresourcemanager/v1/projects", True),
    ]
