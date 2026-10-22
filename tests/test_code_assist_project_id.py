import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.google_oauth_api import (
    Credentials,
    _try_load_code_assist,
    _try_onboard_user,
    fetch_project_id_and_tier,
)


async def test_load_code_assist_sends_project_id_and_falls_back_when_omitted_in_response():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "{}"
    mock_response.json.return_value = {
        "currentTier": {
            "id": "standard-tier",
            "userDefinedCloudaicompanionProject": True,
        },
        "paidTier": {
            "id": "gcp-standard-tier",
            "userDefinedCloudaicompanionProject": True,
        },
    }

    with patch("src.google_oauth_api.post_async", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        project_id, tier, credits = await _try_load_code_assist(
            "https://cloudcode-pa.googleapis.com",
            {"Authorization": "Bearer mock-token"},
            project_id="my-licensed-project",
        )

    assert project_id == "my-licensed-project"
    assert tier == "gcp-standard-tier"
    assert credits is None
    sent_json = mock_post.call_args.kwargs["json"]
    assert sent_json["cloudaicompanionProject"] == "my-licensed-project"
    assert sent_json["metadata"]["duetProject"] == "my-licensed-project"


async def test_fetch_project_id_and_tier_auto_detects_user_project_for_licensed_account():
    # First call without project_id returns no currentTier and no cloudaicompanionProject
    first_resp = MagicMock()
    first_resp.status_code = 200
    first_resp.text = "{}"
    first_resp.json.return_value = {
        "allowedTiers": [
            {
                "id": "standard-tier",
                "userDefinedCloudaicompanionProject": True,
                "isDefault": True,
            }
        ],
        "paidTier": {
            "id": "gcp-standard-tier",
            "userDefinedCloudaicompanionProject": True,
        },
    }

    # Second call with auto-detected project_id returns activated currentTier
    second_resp = MagicMock()
    second_resp.status_code = 200
    second_resp.text = "{}"
    second_resp.json.return_value = {
        "currentTier": {"id": "standard-tier"},
        "cloudaicompanionProject": "my-licensed-project",
        "paidTier": {"id": "gcp-standard-tier"},
    }

    with (
        patch(
            "src.google_oauth_api.post_async",
            new_callable=AsyncMock,
            side_effect=[first_resp, second_resp],
        ) as mock_post,
        patch(
            "src.google_oauth_api.get_user_projects",
            new_callable=AsyncMock,
            return_value=[{"projectId": "my-licensed-project", "lifecycleState": "ACTIVE"}],
        ),
    ):
        project_id, tier = await fetch_project_id_and_tier(
            access_token="mock-token",
            user_agent="mock-ua",
            api_base_url="https://cloudcode-pa.googleapis.com",
        )

    assert project_id == "my-licensed-project"
    assert tier == "pro"
    assert mock_post.call_count == 2
    second_json = mock_post.call_args_list[1].kwargs["json"]
    assert second_json["cloudaicompanionProject"] == "my-licensed-project"
    assert second_json["metadata"]["duetProject"] == "my-licensed-project"


async def test_onboard_user_passes_project_id_for_non_free_tier():
    tier_resp = MagicMock()
    tier_resp.status_code = 200
    tier_resp.json.return_value = {
        "allowedTiers": [{"id": "standard-tier", "isDefault": True}]
    }

    onboard_resp = MagicMock()
    onboard_resp.status_code = 200
    onboard_resp.json.return_value = {
        "done": True,
        "response": {},
    }

    with patch(
        "src.google_oauth_api.post_async",
        new_callable=AsyncMock,
        side_effect=[tier_resp, onboard_resp],
    ) as mock_post:
        resolved_project = await _try_onboard_user(
            "https://cloudcode-pa.googleapis.com",
            {"Authorization": "Bearer mock-token"},
            project_id="my-licensed-project",
        )

    assert resolved_project == "my-licensed-project"
    onboard_json = mock_post.call_args_list[1].kwargs["json"]
    assert onboard_json["cloudaicompanionProject"] == "my-licensed-project"
    assert onboard_json["metadata"]["duetProject"] == "my-licensed-project"


def test_credentials_from_dict_supports_quota_project_id():
    creds = Credentials.from_dict(
        {
            "access_token": "mock-token",
            "quota_project_id": "my-quota-project",
        }
    )
    assert creds.project_id == "my-quota-project"


if __name__ == "__main__":
    asyncio.run(
        test_load_code_assist_sends_project_id_and_falls_back_when_omitted_in_response()
    )
    asyncio.run(
        test_fetch_project_id_and_tier_auto_detects_user_project_for_licensed_account()
    )
    asyncio.run(test_onboard_user_passes_project_id_for_non_free_tier())
    test_credentials_from_dict_supports_quota_project_id()
    print("All tests passed!")
