"""Integration-style tests for monitoring data retrieval."""

# pylint: disable=import-error
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
