"""GoTrue admin create-user helper."""

from unittest.mock import MagicMock, patch

import pytest

from news_manager.auth_admin import (
    AuthAdminDuplicateEmail,
    AuthAdminError,
    AuthAdminUnauthorized,
    create_auth_user_with_password,
)


@patch("news_manager.auth_admin.httpx.Client")
def test_create_auth_user_with_password_ok(mock_client_cls: MagicMock) -> None:
    http_inst = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = http_inst
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "email": "u@example.com"}
    http_inst.post.return_value = resp

    uid = create_auth_user_with_password(
        supabase_url="https://proj.supabase.co",
        service_role_key="service-key",
        email="u@example.com",
        password="secret123",
    )
    assert uid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    http_inst.post.assert_called_once()
    args, kwargs = http_inst.post.call_args
    assert args[0] == "https://proj.supabase.co/auth/v1/admin/users"
    assert kwargs["json"]["email"] == "u@example.com"
    assert kwargs["json"]["password"] == "secret123"
    assert kwargs["json"]["email_confirm"] is True


@patch("news_manager.auth_admin.httpx.Client")
def test_create_auth_user_with_password_401(mock_client_cls: MagicMock) -> None:
    http_inst = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = http_inst
    resp = MagicMock()
    resp.status_code = 401
    http_inst.post.return_value = resp

    with pytest.raises(AuthAdminUnauthorized):
        create_auth_user_with_password(
            supabase_url="https://proj.supabase.co",
            service_role_key="bad-key",
            email="u@example.com",
            password="secret123",
        )


@patch("news_manager.auth_admin.httpx.Client")
def test_create_auth_user_with_password_duplicate_409(mock_client_cls: MagicMock) -> None:
    http_inst = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = http_inst
    resp = MagicMock()
    resp.status_code = 409
    http_inst.post.return_value = resp

    with pytest.raises(AuthAdminDuplicateEmail):
        create_auth_user_with_password(
            supabase_url="https://proj.supabase.co",
            service_role_key="service-key",
            email="u@example.com",
            password="secret123",
        )


@patch("news_manager.auth_admin.httpx.Client")
def test_create_auth_user_with_password_duplicate_422_message(mock_client_cls: MagicMock) -> None:
    http_inst = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = http_inst
    resp = MagicMock()
    resp.status_code = 422
    resp.text = "User already registered"
    resp.json.return_value = {"msg": "already registered"}
    http_inst.post.return_value = resp

    with pytest.raises(AuthAdminDuplicateEmail):
        create_auth_user_with_password(
            supabase_url="https://proj.supabase.co",
            service_role_key="service-key",
            email="u@example.com",
            password="secret123",
        )


@patch("news_manager.auth_admin.httpx.Client")
def test_create_auth_user_with_password_422_other(mock_client_cls: MagicMock) -> None:
    http_inst = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = http_inst
    resp = MagicMock()
    resp.status_code = 422
    resp.text = "Password too weak"
    resp.json.return_value = {"msg": "Password too weak"}
    http_inst.post.return_value = resp

    with pytest.raises(AuthAdminError, match="HTTP 422"):
        create_auth_user_with_password(
            supabase_url="https://proj.supabase.co",
            service_role_key="service-key",
            email="u@example.com",
            password="123",
        )
