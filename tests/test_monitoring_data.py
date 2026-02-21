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
# This file was created or modified with the assistance of an AI (Large Language Model).
# Review required for correctness, security, and licensing.

"""Integration-style tests for monitoring data retrieval."""

# pylint: disable=import-error,duplicate-code
import datetime

import pytest

import server


@pytest.fixture()
def client(tmp_path):
    """Create a test client with a temporary database."""
    server.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    with server.app.app_context():
        server.db.create_all()
        server.current_targets = []
        server.recent_cache.clear()
        server._last_purge_time = None  # pylint: disable=protected-access
        yield server.app.test_client()
        server.db.session.remove()
        server.db.drop_all()


def test_get_monitoring_data_returns_recent_entries_from_db(
    client,
):  # pylint: disable=redefined-outer-name
    """Ensure monitoring endpoint filters out old entries from database."""
    now = datetime.datetime.utcnow()
    recent = now - datetime.timedelta(minutes=10)
    old = now - datetime.timedelta(hours=2)

    with server.app.app_context():
        server.db.session.add(
            server.MonitoringData(
                agent_id="agent-1",
                target="8.8.8.8",
                timestamp=recent,
                result="ok",
                latency=12.3,
            )
        )
        server.db.session.add(
            server.MonitoringData(
                agent_id="agent-1",
                target="8.8.8.8",
                timestamp=old,
                result="ok",
                latency=45.6,
            )
        )
        server.db.session.commit()

    response = client.get("/monitoring/agent-1/8.8.8.8")

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]["latency"] == 12.3


def test_get_monitoring_data_returns_jitter_and_packet_loss(
    client,
):  # pylint: disable=redefined-outer-name
    """Ensure monitoring endpoint returns jitter and packet_loss fields."""
    now = datetime.datetime.utcnow()
    ts = now - datetime.timedelta(minutes=5)

    with server.app.app_context():
        server.db.session.add(
            server.MonitoringData(
                agent_id="agent-2",
                target="1.1.1.1",
                timestamp=ts,
                result="ok",
                latency=20.0,
                jitter=2.5,
                packet_loss=10.0,
            )
        )
        server.db.session.commit()

    response = client.get("/monitoring/agent-2/1.1.1.1")

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]["jitter"] == 2.5
    assert data[0]["packet_loss"] == 10.0


# ── Data retention / purge tests ─────────────────────────────────────────────


def test_purge_old_monitoring_data_removes_expired_rows(
    client,
):  # pylint: disable=redefined-outer-name,unused-argument
    """purge_old_monitoring_data() must delete rows outside the retention window."""
    now = datetime.datetime.utcnow()
    recent = now - datetime.timedelta(hours=1)
    expired = now - datetime.timedelta(hours=25)

    with server.app.app_context():
        server.db.session.add(
            server.MonitoringData(
                agent_id="agent-purge",
                target="192.0.2.1",
                timestamp=recent,
                result="ok",
                latency=5.0,
            )
        )
        server.db.session.add(
            server.MonitoringData(
                agent_id="agent-purge",
                target="192.0.2.1",
                timestamp=expired,
                result="ok",
                latency=99.0,
            )
        )
        server.db.session.commit()

        deleted = server.purge_old_monitoring_data()
        remaining = server.MonitoringData.query.filter_by(agent_id="agent-purge").count()

    assert deleted == 1
    assert remaining == 1


def test_purge_old_monitoring_data_keeps_all_rows_within_window(
    client,
):  # pylint: disable=redefined-outer-name,unused-argument
    """purge_old_monitoring_data() must keep all rows inside the retention window."""
    now = datetime.datetime.utcnow()

    with server.app.app_context():
        for offset_h in [1, 12, 23]:
            server.db.session.add(
                server.MonitoringData(
                    agent_id="agent-keep",
                    target="192.0.2.2",
                    timestamp=now - datetime.timedelta(hours=offset_h),
                    result="ok",
                    latency=10.0,
                )
            )
        server.db.session.commit()

        deleted = server.purge_old_monitoring_data()
        remaining = server.MonitoringData.query.filter_by(agent_id="agent-keep").count()

    assert deleted == 0
    assert remaining == 3


# ── Analytics endpoint tests ──────────────────────────────────────────────────


def test_get_monitoring_analytics_returns_hourly_buckets(
    client,
):  # pylint: disable=redefined-outer-name
    """Analytics endpoint must return one bucket per hour with aggregated stats."""
    now = datetime.datetime.utcnow().replace(minute=30, second=0, microsecond=0)
    hour_bucket = now.replace(minute=0)

    with server.app.app_context():
        for latency in [10.0, 20.0, 30.0]:
            server.db.session.add(
                server.MonitoringData(
                    agent_id="agent-ana",
                    target="10.0.0.1",
                    timestamp=now,
                    result="ok",
                    latency=latency,
                    jitter=1.0,
                    packet_loss=0.0,
                )
            )
        server.db.session.commit()

    response = client.get("/monitoring/agent-ana/10.0.0.1/analytics")

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    bucket = data[0]
    assert bucket["hour"] == hour_bucket.isoformat()
    assert bucket["total_samples"] == 3
    assert bucket["success_count"] == 3
    assert abs(bucket["avg_latency"] - 20.0) < 1e-6


def test_get_monitoring_analytics_excludes_expired_rows(
    client,
):  # pylint: disable=redefined-outer-name
    """Analytics endpoint must not return data outside the retention window."""
    now = datetime.datetime.utcnow()
    expired = now - datetime.timedelta(hours=25)

    with server.app.app_context():
        server.db.session.add(
            server.MonitoringData(
                agent_id="agent-exp",
                target="10.0.0.2",
                timestamp=expired,
                result="ok",
                latency=50.0,
            )
        )
        server.db.session.commit()

    response = client.get("/monitoring/agent-exp/10.0.0.2/analytics")

    assert response.status_code == 200
    data = response.get_json()
    assert data == []
