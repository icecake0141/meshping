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

"""Tests for template-based monitoring configuration (customer/line profiles)."""

# pylint: disable=import-error
import server


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------


def test_list_templates_returns_empty_list(client):
    """GET /admin/templates returns an empty list when no templates exist."""
    response = client.get("/admin/templates")
    assert response.status_code == 200
    assert response.get_json() == []


def test_create_template_returns_201(client):
    """POST /admin/templates creates a template and returns 201."""
    payload = {"name": "customer-A", "targets": ["10.0.0.1", "10.0.0.2"]}
    response = client.post("/admin/templates", json=payload)
    assert response.status_code == 201
    data = response.get_json()
    assert data["name"] == "customer-A"
    assert data["targets"] == ["10.0.0.1", "10.0.0.2"]
    assert "id" in data


def test_create_template_missing_fields_returns_400(client):
    """POST /admin/templates without required fields returns 400."""
    response = client.post("/admin/templates", json={"name": "x"})
    assert response.status_code == 400

    response = client.post("/admin/templates", json={"targets": ["10.0.0.1"]})
    assert response.status_code == 400


def test_create_template_duplicate_name_returns_409(client):
    """POST /admin/templates with a duplicate name returns 409."""
    payload = {"name": "dup", "targets": []}
    client.post("/admin/templates", json=payload)
    response = client.post("/admin/templates", json=payload)
    assert response.status_code == 409


def test_create_template_non_string_targets_returns_400(client):
    """POST /admin/templates with non-string targets returns 400."""
    response = client.post(
        "/admin/templates", json={"name": "bad", "targets": ["10.0.0.1", 99]}
    )
    assert response.status_code == 400


def test_get_template_returns_template(client):
    """GET /admin/templates/<id> returns the requested template."""
    create_resp = client.post(
        "/admin/templates", json={"name": "line-1", "targets": ["192.168.1.1"]}
    )
    tmpl_id = create_resp.get_json()["id"]

    response = client.get(f"/admin/templates/{tmpl_id}")
    assert response.status_code == 200
    assert response.get_json()["name"] == "line-1"


def test_get_template_not_found_returns_404(client):
    """GET /admin/templates/<id> for unknown ID returns 404."""
    response = client.get("/admin/templates/9999")
    assert response.status_code == 404


def test_update_template_targets(client):
    """PUT /admin/templates/<id> updates the target list."""
    create_resp = client.post(
        "/admin/templates", json={"name": "tmpl", "targets": ["10.0.0.1"]}
    )
    tmpl_id = create_resp.get_json()["id"]

    response = client.put(
        f"/admin/templates/{tmpl_id}", json={"targets": ["10.1.1.1", "10.2.2.2"]}
    )
    assert response.status_code == 200
    assert response.get_json()["targets"] == ["10.1.1.1", "10.2.2.2"]


def test_update_template_name(client):
    """PUT /admin/templates/<id> can rename the template."""
    create_resp = client.post(
        "/admin/templates", json={"name": "old-name", "targets": []}
    )
    tmpl_id = create_resp.get_json()["id"]

    response = client.put(f"/admin/templates/{tmpl_id}", json={"name": "new-name"})
    assert response.status_code == 200
    assert response.get_json()["name"] == "new-name"


def test_update_template_duplicate_name_returns_409(client):
    """PUT /admin/templates/<id> with a name already used by another template returns 409."""
    client.post("/admin/templates", json={"name": "alpha", "targets": []})
    resp2 = client.post("/admin/templates", json={"name": "beta", "targets": []})
    beta_id = resp2.get_json()["id"]

    response = client.put(f"/admin/templates/{beta_id}", json={"name": "alpha"})
    assert response.status_code == 409


def test_update_template_not_found_returns_404(client):
    """PUT /admin/templates/<id> for unknown ID returns 404."""
    response = client.put("/admin/templates/9999", json={"name": "x"})
    assert response.status_code == 404


def test_delete_template(client):
    """DELETE /admin/templates/<id> removes the template."""
    create_resp = client.post(
        "/admin/templates", json={"name": "to-delete", "targets": []}
    )
    tmpl_id = create_resp.get_json()["id"]

    response = client.delete(f"/admin/templates/{tmpl_id}")
    assert response.status_code == 200

    response = client.get(f"/admin/templates/{tmpl_id}")
    assert response.status_code == 404


def test_delete_template_not_found_returns_404(client):
    """DELETE /admin/templates/<id> for unknown ID returns 404."""
    response = client.delete("/admin/templates/9999")
    assert response.status_code == 404


def test_list_templates_returns_all(client):
    """GET /admin/templates lists all created templates."""
    client.post("/admin/templates", json={"name": "t1", "targets": []})
    client.post("/admin/templates", json={"name": "t2", "targets": []})

    response = client.get("/admin/templates")
    assert response.status_code == 200
    names = [t["name"] for t in response.get_json()]
    assert "t1" in names
    assert "t2" in names


# ---------------------------------------------------------------------------
# Agent template assignment
# ---------------------------------------------------------------------------


def _create_approved_agent(_client) -> int:  # pylint: disable=unused-argument
    """Helper: register and approve an agent, returning its DB id."""
    with server.app.app_context():
        agent = server.Agent(
            hostname="test-host",
            ip_address="192.168.0.1",
            version="1.0.0",
            passphrase="secret",
            status="approved",
            agent_id="agent_test",
        )
        server.db.session.add(agent)
        server.db.session.commit()
        return agent.id


def test_assign_template_to_agent(client):
    """POST /admin/agents/<id>/assign_template assigns a template to an agent."""
    agent_id = _create_approved_agent(client)

    create_resp = client.post(
        "/admin/templates", json={"name": "profile-X", "targets": ["8.8.8.8"]}
    )
    tmpl_id = create_resp.get_json()["id"]

    response = client.post(
        f"/admin/agents/{agent_id}/assign_template", json={"template_id": tmpl_id}
    )
    assert response.status_code == 200
    assert response.get_json()["template_id"] == tmpl_id


def test_assign_template_unknown_agent_returns_404(client):
    """POST /admin/agents/<id>/assign_template for unknown agent returns 404."""
    response = client.post(
        "/admin/agents/9999/assign_template", json={"template_id": None}
    )
    assert response.status_code == 404


def test_assign_unknown_template_returns_404(client):
    """POST /admin/agents/<id>/assign_template with unknown template_id returns 404."""
    agent_id = _create_approved_agent(client)
    response = client.post(
        f"/admin/agents/{agent_id}/assign_template", json={"template_id": 9999}
    )
    assert response.status_code == 404


def test_unassign_template_from_agent(client):
    """Setting template_id to null removes the template assignment."""
    agent_id = _create_approved_agent(client)

    create_resp = client.post(
        "/admin/templates", json={"name": "tmp", "targets": ["1.1.1.1"]}
    )
    tmpl_id = create_resp.get_json()["id"]

    client.post(
        f"/admin/agents/{agent_id}/assign_template", json={"template_id": tmpl_id}
    )

    response = client.post(
        f"/admin/agents/{agent_id}/assign_template", json={"template_id": None}
    )
    assert response.status_code == 200
    assert response.get_json()["template_id"] is None


def test_assign_template_missing_field_returns_400(client):
    """POST /admin/agents/<id>/assign_template without template_id returns 400."""
    agent_id = _create_approved_agent(client)
    response = client.post(f"/admin/agents/{agent_id}/assign_template", json={})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# _get_targets_for_agent helper
# ---------------------------------------------------------------------------


def test_get_targets_for_agent_without_template_returns_global(client):  # pylint: disable=unused-argument
    """Agent without a template receives global current_targets."""
    with server.app.app_context():
        server.current_targets = ["172.16.0.1"]
        agent = server.Agent(
            hostname="h",
            ip_address="10.0.0.1",
            version="1.0",
            passphrase="p",
            status="approved",
        )
        assert server._get_targets_for_agent(agent) == ["172.16.0.1"]  # pylint: disable=protected-access


def test_get_targets_for_agent_with_template_returns_template_targets(client):  # pylint: disable=unused-argument
    """Agent with an assigned template receives the template's target list."""
    with server.app.app_context():
        server.current_targets = ["172.16.0.1"]

        tmpl = server.MonitoringTemplate(name="my-tmpl")
        tmpl.targets = ["10.10.10.1", "10.10.10.2"]
        server.db.session.add(tmpl)
        server.db.session.flush()

        agent = server.Agent(
            hostname="h",
            ip_address="10.0.0.1",
            version="1.0",
            passphrase="p",
            status="approved",
            template_id=tmpl.id,
            template=tmpl,
        )
        assert server._get_targets_for_agent(agent) == ["10.10.10.1", "10.10.10.2"]  # pylint: disable=protected-access


def test_delete_template_clears_agent_assignment(client):
    """Deleting a template clears template_id on associated agents."""
    agent_id = _create_approved_agent(client)

    create_resp = client.post(
        "/admin/templates", json={"name": "to-clear", "targets": ["9.9.9.9"]}
    )
    tmpl_id = create_resp.get_json()["id"]
    client.post(
        f"/admin/agents/{agent_id}/assign_template", json={"template_id": tmpl_id}
    )

    client.delete(f"/admin/templates/{tmpl_id}")

    with server.app.app_context():
        agent = server.Agent.query.get(agent_id)
        assert agent.template_id is None
