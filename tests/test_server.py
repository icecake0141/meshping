"""
Test suite for meshping server application.

This module contains unit tests for the Flask server endpoints
and functionality related to monitoring target management.
"""

# pylint: disable=import-error
import pytest

import server


@pytest.fixture()
def client(tmp_path):
    """Create a test client for the Flask application with a temporary database."""
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


def test_update_targets_without_json_body_returns_400(
    client,
):  # pylint: disable=redefined-outer-name
    """Test that updating targets without a JSON body returns 400."""
    response = client.post("/admin/update_targets")

    assert response.status_code == 400


def test_update_targets_without_targets_field_returns_400(
    client,
):  # pylint: disable=redefined-outer-name
    """Test that updating targets without the 'targets' field returns 400."""
    response = client.post("/admin/update_targets", json={"name": "missing"})

    assert response.status_code == 400


def test_update_targets_with_targets_returns_200_and_targets(
    client,
):  # pylint: disable=redefined-outer-name
    """Test that updating targets with valid data returns 200 and the targets list."""
    payload = {"targets": ["10.0.0.1", "10.0.0.2"]}

    response = client.post("/admin/update_targets", json=payload)

    assert response.status_code == 200
    data = response.get_json()
    assert data["targets"] == payload["targets"]


def test_update_targets_with_non_list_targets_returns_400(
    client,
):  # pylint: disable=redefined-outer-name
    """Test that updating targets with non-list data returns 400."""
    payload = {"targets": "not a list"}

    response = client.post("/admin/update_targets", json=payload)

    assert response.status_code == 400


def test_update_targets_with_non_string_elements_returns_400(
    client,
):  # pylint: disable=redefined-outer-name
    """Test that updating targets with non-string elements returns 400."""
    payload = {"targets": ["10.0.0.1", 123, "10.0.0.3"]}

    response = client.post("/admin/update_targets", json=payload)

    assert response.status_code == 400
