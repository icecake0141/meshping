# Copyright 2026 Meshping Contributors
# SPDX-License-Identifier: Apache-2.0
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
# This file was created or modified with the assistance of an AI (Large Language Model).
# Review required for correctness, security, and licensing.

"""Tests for agent update policy management APIs."""

# pylint: disable=import-error,redefined-outer-name
import server


def test_get_update_policy_returns_empty_payload(client):
    """GET /admin/update_policy returns empty payload when no policy exists."""
    resp = client.get("/admin/update_policy")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["target_version"] is None
    assert data["download_url"] is None
    assert data["mandatory"] is False


def test_set_update_policy_requires_fields(client):
    """POST /admin/update_policy validates required fields."""
    resp = client.post("/admin/update_policy", json={"target_version": "1.2.0"})
    assert resp.status_code == 400


def test_update_policy_marks_agents_needing_update(client):
    """Agent versions should reflect update_required when behind policy."""
    with server.app.app_context():
        server.db.session.add(
            server.Agent(
                agent_id="agent-old",
                hostname="host-old",
                ip_address="10.0.0.1",
                version="1.0.0",
                passphrase="pass-old",
                status="approved",
            )
        )
        server.db.session.add(
            server.Agent(
                agent_id="agent-new",
                hostname="host-new",
                ip_address="10.0.0.2",
                version="1.1.0",
                passphrase="pass-new",
                status="approved",
            )
        )
        server.db.session.commit()

    payload = {
        "target_version": "1.1.0",
        "download_url": "https://example.com/meshping-agent",
        "mandatory": True,
    }
    create_resp = client.post("/admin/update_policy", json=payload)
    assert create_resp.status_code == 201

    policy_resp = client.get("/admin/update_policy")
    assert policy_resp.status_code == 200
    assert policy_resp.get_json()["target_version"] == "1.1.0"

    versions_resp = client.get("/admin/agent_versions")
    assert versions_resp.status_code == 200
    data = {row["agent_id"]: row for row in versions_resp.get_json()}
    assert data["agent-old"]["update_required"] is True
    assert data["agent-new"]["update_required"] is False
    assert data["agent-old"]["target_version"] == "1.1.0"
