from fastapi.testclient import TestClient

from tests.conftest import TEST_ADMIN_PASSWORD, login_as


def test_change_password_rejects_same(auth_client: TestClient):
    r = auth_client.post(
        "/api/auth/password",
        json={"password": TEST_ADMIN_PASSWORD, "new_password": TEST_ADMIN_PASSWORD},
    )
    assert r.status_code == 400
    assert "相同" in r.json()["detail"]


def test_change_password_wrong_current(auth_client: TestClient):
    r = auth_client.post(
        "/api/auth/password",
        json={"password": "not-the-password", "new_password": "newpass1"},
    )
    assert r.status_code == 400
    assert "当前密码" in r.json()["detail"]


def test_change_password_then_login(auth_client: TestClient):
    r = auth_client.post(
        "/api/auth/password",
        json={"password": TEST_ADMIN_PASSWORD, "new_password": "newpass1"},
    )
    assert r.status_code == 200
    body = login_as(auth_client, "admin", "newpass1")
    assert body["username"] == "admin"
    # Restore for later tests in the same session DB.
    r2 = auth_client.post(
        "/api/auth/password",
        json={"password": "newpass1", "new_password": TEST_ADMIN_PASSWORD},
    )
    assert r2.status_code == 200
