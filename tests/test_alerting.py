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
# NOTE: This file was created with the assistance of an AI (Large Language Model).
# Review required for correctness, security, and licensing.

"""
Unit tests for the alerting and escalation functionality.

Tests cover:
  - AlertRule CRUD endpoints
  - evaluate_alert logic (consecutive failure counting, threshold crossing,
    recovery notifications, latency-based alerting)
  - Webhook notification helper
"""

# pylint: disable=import-error,redefined-outer-name
import pytest

import server


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path):
    """Flask test client with isolated in-memory database."""
    server.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'alert_test.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    with server.app.app_context():
        server.db.create_all()
        server.current_targets = []
        server.recent_cache.clear()
        yield server.app.test_client()
        server.db.session.remove()
        server.db.drop_all()


# ── AlertRule API tests ───────────────────────────────────────────────────────


def test_list_alert_rules_empty(client):
    """GET /admin/alert_rules returns empty list when no rules exist."""
    resp = client.get("/admin/alert_rules")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_create_alert_rule_returns_201(client):
    """POST /admin/alert_rules creates a rule and returns 201."""
    resp = client.post(
        "/admin/alert_rules",
        json={"target": "8.8.8.8", "consecutive_failures": 3},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["target"] == "8.8.8.8"
    assert data["consecutive_failures"] == 3
    assert data["id"] is not None


def test_create_alert_rule_missing_target_returns_400(client):
    """POST /admin/alert_rules without 'target' returns 400."""
    resp = client.post("/admin/alert_rules", json={"consecutive_failures": 2})
    assert resp.status_code == 400


def test_create_alert_rule_invalid_consecutive_failures_returns_400(client):
    """POST /admin/alert_rules with consecutive_failures < 1 returns 400."""
    resp = client.post(
        "/admin/alert_rules",
        json={"target": "1.1.1.1", "consecutive_failures": 0},
    )
    assert resp.status_code == 400


def test_create_alert_rule_invalid_latency_threshold_returns_400(client):
    """POST /admin/alert_rules with negative latency_threshold_ms returns 400."""
    resp = client.post(
        "/admin/alert_rules",
        json={"target": "1.1.1.1", "latency_threshold_ms": -10},
    )
    assert resp.status_code == 400


def test_list_alert_rules_returns_created_rule(client):
    """GET /admin/alert_rules returns previously created rules."""
    client.post(
        "/admin/alert_rules",
        json={"target": "192.168.1.1", "consecutive_failures": 5},
    )
    resp = client.get("/admin/alert_rules")
    assert resp.status_code == 200
    rules = resp.get_json()
    assert len(rules) == 1
    assert rules[0]["target"] == "192.168.1.1"
    assert rules[0]["consecutive_failures"] == 5


def test_delete_alert_rule(client):
    """DELETE /admin/alert_rules/<id> removes the rule."""
    create_resp = client.post(
        "/admin/alert_rules",
        json={"target": "10.0.0.1", "consecutive_failures": 2},
    )
    rule_id = create_resp.get_json()["id"]

    del_resp = client.delete(f"/admin/alert_rules/{rule_id}")
    assert del_resp.status_code == 200

    list_resp = client.get("/admin/alert_rules")
    assert list_resp.get_json() == []


def test_delete_nonexistent_alert_rule_returns_404(client):
    """DELETE /admin/alert_rules/<id> for unknown id returns 404."""
    resp = client.delete("/admin/alert_rules/9999")
    assert resp.status_code == 404


# ── evaluate_alert unit tests ─────────────────────────────────────────────────


def test_evaluate_alert_no_rule_does_nothing(client):
    """evaluate_alert is a no-op when no matching rule exists."""
    with server.app.app_context():
        # No rules in DB; should not raise or create AlertState rows.
        server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
        assert server.AlertState.query.count() == 0


def test_evaluate_alert_fires_alert_after_threshold(client):
    """evaluate_alert raises an alert once consecutive failures reach the threshold."""
    with server.app.app_context():
        server.db.session.add(
            server.AlertRule(target="8.8.8.8", consecutive_failures=3)
        )
        server.db.session.commit()

        sent_payloads = []

        def fake_send(url, payload):
            sent_payloads.append(payload)

        original = server._send_webhook  # pylint: disable=protected-access
        server._send_webhook = fake_send  # pylint: disable=protected-access
        try:
            # First two failures: no alert yet.
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
            assert sent_payloads == []

            # Third failure: alert should fire.
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
            assert len(sent_payloads) == 1
            assert sent_payloads[0]["event"] == "alert"
            assert sent_payloads[0]["target"] == "8.8.8.8"
        finally:
            server._send_webhook = original  # pylint: disable=protected-access


def test_evaluate_alert_no_duplicate_alert_while_alerting(client):
    """evaluate_alert does not send duplicate alerts once already in alerting state."""
    with server.app.app_context():
        server.db.session.add(
            server.AlertRule(target="8.8.8.8", consecutive_failures=2)
        )
        server.db.session.commit()

        sent_payloads = []

        def fake_send(url, payload):
            sent_payloads.append(payload)

        original = server._send_webhook  # pylint: disable=protected-access
        server._send_webhook = fake_send  # pylint: disable=protected-access
        try:
            # Reach alerting state.
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
            assert len(sent_payloads) == 1

            # Additional failures should not send more alerts.
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
            assert len(sent_payloads) == 1
        finally:
            server._send_webhook = original  # pylint: disable=protected-access


def test_evaluate_alert_sends_recovery_on_success(client):
    """evaluate_alert sends a recovery notification when the target recovers."""
    with server.app.app_context():
        server.db.session.add(
            server.AlertRule(target="8.8.8.8", consecutive_failures=2)
        )
        server.db.session.commit()

        sent_payloads = []

        def fake_send(url, payload):
            sent_payloads.append(payload)

        original = server._send_webhook  # pylint: disable=protected-access
        server._send_webhook = fake_send  # pylint: disable=protected-access
        try:
            # Trigger alert.
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)
            server.evaluate_alert("agent-1", "8.8.8.8", "fail", 0)

            # Recovery.
            server.evaluate_alert("agent-1", "8.8.8.8", "ok", 5.0)

            assert len(sent_payloads) == 2
            assert sent_payloads[1]["event"] == "recovery"
        finally:
            server._send_webhook = original  # pylint: disable=protected-access


def test_evaluate_alert_latency_threshold(client):
    """evaluate_alert fires when latency exceeds the configured threshold."""
    with server.app.app_context():
        server.db.session.add(
            server.AlertRule(
                target="8.8.8.8",
                consecutive_failures=1,
                latency_threshold_ms=100.0,
            )
        )
        server.db.session.commit()

        sent_payloads = []

        def fake_send(url, payload):
            sent_payloads.append(payload)

        original = server._send_webhook  # pylint: disable=protected-access
        server._send_webhook = fake_send  # pylint: disable=protected-access
        try:
            # Latency below threshold: no alert.
            server.evaluate_alert("agent-1", "8.8.8.8", "ok", 50.0)
            assert sent_payloads == []

            # Latency above threshold: alert.
            server.evaluate_alert("agent-1", "8.8.8.8", "ok", 200.0)
            assert len(sent_payloads) == 1
            assert sent_payloads[0]["event"] == "alert"
        finally:
            server._send_webhook = original  # pylint: disable=protected-access


def test_evaluate_alert_wildcard_rule_matches_any_target(client):
    """A rule with target="*" matches any target."""
    with server.app.app_context():
        server.db.session.add(
            server.AlertRule(target="*", consecutive_failures=1)
        )
        server.db.session.commit()

        sent_payloads = []

        def fake_send(url, payload):
            sent_payloads.append(payload)

        original = server._send_webhook  # pylint: disable=protected-access
        server._send_webhook = fake_send  # pylint: disable=protected-access
        try:
            server.evaluate_alert("agent-1", "192.168.1.99", "fail", 0)
            assert len(sent_payloads) == 1
            assert sent_payloads[0]["event"] == "alert"
        finally:
            server._send_webhook = original  # pylint: disable=protected-access


# ── /admin/alert_states API tests ────────────────────────────────────────────


def test_list_alert_states_empty(client):
    """GET /admin/alert_states returns empty list when no state exists."""
    resp = client.get("/admin/alert_states")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_alert_states_shows_alerting_entry(client):
    """GET /admin/alert_states reflects current alerting state."""
    with server.app.app_context():
        server.db.session.add(
            server.AlertRule(target="10.0.0.1", consecutive_failures=1)
        )
        server.db.session.commit()

        def _noop(url, payload):
            pass

        original = server._send_webhook  # pylint: disable=protected-access
        server._send_webhook = _noop  # pylint: disable=protected-access
        try:
            server.evaluate_alert("agent-1", "10.0.0.1", "fail", 0)
            server.db.session.commit()
        finally:
            server._send_webhook = original  # pylint: disable=protected-access

    resp = client.get("/admin/alert_states")
    states = resp.get_json()
    assert len(states) == 1
    assert states[0]["status"] == "alerting"
    assert states[0]["target"] == "10.0.0.1"
