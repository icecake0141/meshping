import pytest

import server


@pytest.fixture()
def client(tmp_path):
    server.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    with server.app.app_context():
        server.db.create_all()
        server.current_targets = []
        yield server.app.test_client()
        server.db.session.remove()
        server.db.drop_all()


def test_update_targets_without_json_body_returns_400(client):
    response = client.post("/admin/update_targets")

    assert response.status_code == 400


def test_update_targets_without_targets_field_returns_400(client):
    response = client.post("/admin/update_targets", json={"name": "missing"})

    assert response.status_code == 400


def test_update_targets_with_targets_returns_200_and_targets(client):
    payload = {"targets": ["10.0.0.1", "10.0.0.2"]}

    response = client.post("/admin/update_targets", json=payload)

    assert response.status_code == 200
    data = response.get_json()
    assert data["targets"] == payload["targets"]
