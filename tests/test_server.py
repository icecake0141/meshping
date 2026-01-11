# Copyright 2026 Meshping Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# NOTE: This file may include code that was generated or suggested by a large language model (LLM).

"""
Unit tests for the Meshping server application.

This module contains tests for the server's REST API endpoints,
particularly the monitoring target management functionality.
"""
# pylint: disable=import-error
import pytest

import server


@pytest.fixture(name='test_client')
def client(tmp_path):
    """
    Create a Flask test client with a temporary test database.
    
    Args:
        tmp_path: pytest fixture providing a temporary directory path
        
    Yields:
        Flask test client configured for testing
    """
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


def test_update_targets_without_json_body_returns_400(test_client):
    """Test that POST without JSON body returns 400 error."""
    response = test_client.post("/admin/update_targets")

    assert response.status_code == 400


def test_update_targets_without_targets_field_returns_400(test_client):
    """Test that POST without 'targets' field returns 400 error."""
    response = test_client.post("/admin/update_targets", json={"name": "missing"})

    assert response.status_code == 400


def test_update_targets_with_targets_returns_200_and_targets(test_client):
    """Test that valid targets list returns 200 with target data."""

    payload = {"targets": ["10.0.0.1", "10.0.0.2"]}

    response = test_client.post("/admin/update_targets", json=payload)

    assert response.status_code == 200
    data = response.get_json()
    assert data["targets"] == payload["targets"]


def test_update_targets_with_non_list_targets_returns_400(test_client):
    """Test that non-list targets value returns 400 error."""
    payload = {"targets": "not a list"}

    response = test_client.post("/admin/update_targets", json=payload)

    assert response.status_code == 400


def test_update_targets_with_non_string_elements_returns_400(test_client):
    """Test that targets list with non-string elements returns 400 error."""
    payload = {"targets": ["10.0.0.1", 123, "10.0.0.3"]}

    response = test_client.post("/admin/update_targets", json=payload)

    assert response.status_code == 400
