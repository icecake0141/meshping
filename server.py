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

"""
Meshping server application for monitoring network connectivity.

This module implements a Flask-based server that manages monitoring agents
and collects ping data from distributed agents across the network.
"""

# pylint: disable=import-error
import typing
import base64
import datetime
import functools
import logging
import os
import secrets
import urllib.request
import urllib.error
import json as json_module

from flask import Flask, request, jsonify, render_template, redirect, url_for, Response
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "replace_with_a_secure_key")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///meshping.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Admin token for protecting admin endpoints.  Set ADMIN_TOKEN env var to enable.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# Shared secret used to authenticate agent connections.  Set AGENT_SECRET env var to enable.
AGENT_SECRET = os.environ.get("AGENT_SECRET", "")

# Default webhook URL for alert notifications.  Set ALERT_WEBHOOK_URL env var to enable.
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")

# Data retention period in hours.  Set RETENTION_HOURS env var to override (default 24).
RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", "24"))

db = SQLAlchemy(app)
socketio = SocketIO(app)

# Flask アプリケーション設定の直後にグローバル変数を初期化
current_targets = []  # 監視対象IPリストの初期値（空リストで初期化）


def require_admin_auth(f):
    """Decorator that enforces HTTP Basic Auth on admin endpoints when ADMIN_TOKEN is set."""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not ADMIN_TOKEN:
            # Auth not configured — allow access (backward-compatible).
            return f(*args, **kwargs)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                _, _, password = decoded.partition(":")
                if secrets.compare_digest(password, ADMIN_TOKEN):
                    return f(*args, **kwargs)
            except Exception:  # pylint: disable=broad-except
                pass
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Meshping Admin"'},
        )

    return decorated


# DB Models
class MonitoringTemplate(db.Model):  # pylint: disable=too-few-public-methods
    """Database model for monitoring configuration templates (customer/line profiles)."""

    __tablename__ = "monitoring_template"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    # JSON-serialized list of target IP addresses / hostnames.
    targets_json = db.Column(db.Text, default="[]", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    @property
    def targets(self) -> typing.List[str]:
        """Return the target list, deserialised from JSON."""
        return json_module.loads(self.targets_json or "[]")

    @targets.setter
    def targets(self, value: typing.List[str]) -> None:
        self.targets_json = json_module.dumps(value)


class Agent(db.Model):  # pylint: disable=too-few-public-methods
    """Database model for monitoring agents."""

    id = db.Column(db.Integer, primary_key=True)
    # 仮登録時はpassphraseのみが識別子。後にエージェントIDを割り振る。
    agent_id = db.Column(db.String(64), unique=True, nullable=True)
    hostname = db.Column(db.String(128))
    ip_address = db.Column(db.String(64))
    version = db.Column(db.String(32))
    passphrase = db.Column(db.String(128))
    status = db.Column(
        db.String(32), default="pending"
    )  # pending, approved, hold, blacklisted
    # Optional link to a MonitoringTemplate; when set, the agent receives the
    # template's target list instead of the global current_targets list.
    template_id = db.Column(
        db.Integer, db.ForeignKey("monitoring_template.id"), nullable=True
    )
    template = db.relationship("MonitoringTemplate", backref="agents")

    registered_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
    last_seen_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class AgentUpdatePolicy(db.Model):  # pylint: disable=too-few-public-methods
    """Database model storing the latest agent update policy."""

    id = db.Column(db.Integer, primary_key=True)
    target_version = db.Column(db.String(32), nullable=False)
    download_url = db.Column(db.String(256), nullable=False)
    mandatory = db.Column(db.Boolean, default=False, nullable=False)
    release_notes = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class MonitoringData(db.Model):  # pylint: disable=too-few-public-methods
    """Database model for monitoring data collected from agents."""

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.String(64))  # エージェントID
    target = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime)
    result = db.Column(db.String(16))  # "ok" または "fail"
    latency = db.Column(db.Float)  # RTT（成功時） or 0
    jitter = db.Column(db.Float, default=0.0)  # mean absolute jitter in ms (rolling window)
    packet_loss = db.Column(db.Float, default=0.0)  # packet loss percentage (rolling window)


class AlertRule(db.Model):  # pylint: disable=too-few-public-methods
    """Database model for alert threshold rules."""

    id = db.Column(db.Integer, primary_key=True)
    # Target IP address to match, or "*" to match all targets.
    target = db.Column(db.String(64), nullable=False)
    # Number of consecutive failures before an alert is raised.
    consecutive_failures = db.Column(db.Integer, default=3, nullable=False)
    # Latency threshold in milliseconds; 0 disables latency-based alerting.
    latency_threshold_ms = db.Column(db.Float, default=0.0, nullable=False)
    # Optional per-rule webhook URL; overrides ALERT_WEBHOOK_URL when set.
    webhook_url = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class AlertState(db.Model):  # pylint: disable=too-few-public-methods
    """Database model tracking the current alert state per agent+target pair."""

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.String(64), nullable=False)
    target = db.Column(db.String(64), nullable=False)
    # Running count of consecutive failures for this agent+target.
    consecutive_failures = db.Column(db.Integer, default=0, nullable=False)
    # "ok" or "alerting"
    status = db.Column(db.String(16), default="ok", nullable=False)
    last_alert_sent = db.Column(db.DateTime, nullable=True)
    __table_args__ = (db.UniqueConstraint("agent_id", "target", name="uq_agent_target"),)


# メモリ上のキャッシュ（直近1時間分のデータ保持）
recent_cache = {}  # { agent_id: [MonitoringData, ...] }

# Timestamp of the last successful purge run; used to throttle DB cleanup.
_last_purge_time: typing.Optional[datetime.datetime] = None  # pylint: disable=invalid-name
# Minimum interval between purge runs (1 hour by default).
_PURGE_INTERVAL = datetime.timedelta(hours=1)


def purge_old_monitoring_data() -> int:
    """Delete MonitoringData rows older than RETENTION_HOURS.

    Returns the number of rows deleted.  Runs at most once per ``_PURGE_INTERVAL``
    to avoid excessive database load on every incoming data batch.
    """
    global _last_purge_time  # pylint: disable=global-statement
    now = datetime.datetime.utcnow()
    if _last_purge_time is not None and (now - _last_purge_time) < _PURGE_INTERVAL:
        return 0
    _last_purge_time = now
    cutoff = now - datetime.timedelta(hours=RETENTION_HOURS)
    deleted = MonitoringData.query.filter(MonitoringData.timestamp < cutoff).delete()
    db.session.commit()
    if deleted:
        logging.info("Purged %d monitoring rows older than %dh", deleted, RETENTION_HOURS)
    return deleted


def _parse_version_segment(segment: str) -> int:
    """Return the leading numeric portion of a version segment."""
    digits = []
    for char in segment:
        if char.isdigit():
            digits.append(char)
        else:
            break
    return int("".join(digits)) if digits else 0


def _normalize_version(version: typing.Optional[str]) -> typing.Tuple[int, ...]:
    """Normalize a dotted version string into a tuple of integers."""
    if not version:
        return tuple()
    parts = version.split(".")
    return tuple(_parse_version_segment(part) for part in parts if part)


def is_version_behind(current: typing.Optional[str], target: typing.Optional[str]) -> bool:
    """Return True when current version is behind target version."""
    if not target:
        return False
    if not current:
        return True
    current_parts = list(_normalize_version(current))
    target_parts = list(_normalize_version(target))
    max_len = max(len(current_parts), len(target_parts))
    current_parts.extend([0] * (max_len - len(current_parts)))
    target_parts.extend([0] * (max_len - len(target_parts)))
    return tuple(current_parts) < tuple(target_parts)


def _get_latest_update_policy() -> typing.Optional[AgentUpdatePolicy]:
    """Return the most recent update policy, if any."""
    return AgentUpdatePolicy.query.order_by(AgentUpdatePolicy.created_at.desc()).first()


def _get_targets_for_agent(agent: "Agent") -> typing.List[str]:
    """Return the effective target list for *agent*.

    When the agent is bound to a :class:`MonitoringTemplate`, the template's
    own target list is returned.  Otherwise the global ``current_targets`` list
    is used as a fallback (backward-compatible behaviour).
    """
    if agent.template_id is not None and agent.template is not None:
        return agent.template.targets
    return current_targets


def _build_update_payload(policy: AgentUpdatePolicy) -> dict:
    """Serialize update policy into a server message payload."""
    return {
        "type": "agent_update",
        "target_version": policy.target_version,
        "download_url": policy.download_url,
        "mandatory": policy.mandatory,
        "release_notes": policy.release_notes,
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
    }


def _should_notify_update(
    agent_version: typing.Optional[str], policy: typing.Optional[AgentUpdatePolicy]
) -> bool:
    """Return True if the agent should be notified about the update policy."""
    if policy is None:
        return False
    return is_version_behind(agent_version, policy.target_version)


def _send_webhook(url: str, payload: dict) -> None:
    """POST a JSON payload to the given webhook URL; logs errors silently."""
    if not url:
        return
    data = json_module.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
            logging.info("Alert webhook sent to %s: HTTP %s", url, resp.status)
    except urllib.error.URLError as exc:
        logging.error("Alert webhook delivery failed (%s): %s", url, exc)


def _find_matching_rule(target: str) -> typing.Optional["AlertRule"]:
    """Return the first AlertRule that matches *target* (exact match before wildcard)."""
    exact = AlertRule.query.filter_by(target=target).first()
    if exact:
        return exact
    return AlertRule.query.filter_by(target="*").first()


def _get_or_create_alert_state(agent_id: str, target: str) -> AlertState:
    """Return the AlertState row for agent_id+target, creating it if absent."""
    state = AlertState.query.filter_by(agent_id=agent_id, target=target).first()
    if not state:
        state = AlertState(
            agent_id=agent_id,
            target=target,
            consecutive_failures=0,
            status="ok",
        )
        db.session.add(state)
    return state


def evaluate_alert(agent_id: str, target: str, result: str, latency: float) -> None:
    """
    Evaluate alert conditions for a single monitoring entry.

    Raises an alert when consecutive failures reach the configured threshold,
    and sends a recovery notification when the target recovers.  Webhook
    delivery uses the per-rule URL when set, otherwise ALERT_WEBHOOK_URL.
    """
    rule = _find_matching_rule(target)
    if rule is None:
        return

    state = _get_or_create_alert_state(agent_id, target)

    # Determine whether this sample counts as a failure.
    is_failure = result != "ok"
    if (
        not is_failure
        and rule.latency_threshold_ms > 0
        and latency > rule.latency_threshold_ms
    ):
        is_failure = True

    webhook_url = rule.webhook_url or ALERT_WEBHOOK_URL

    if is_failure:
        state.consecutive_failures += 1
        if (
            state.consecutive_failures >= rule.consecutive_failures
            and state.status != "alerting"
        ):
            state.status = "alerting"
            state.last_alert_sent = datetime.datetime.utcnow()
            _send_webhook(
                webhook_url,
                {
                    "event": "alert",
                    "agent_id": agent_id,
                    "target": target,
                    "consecutive_failures": state.consecutive_failures,
                    "timestamp": state.last_alert_sent.isoformat(),
                },
            )
    else:
        if state.status == "alerting":
            state.status = "ok"
            state.consecutive_failures = 0
            _send_webhook(
                webhook_url,
                {
                    "event": "recovery",
                    "agent_id": agent_id,
                    "target": target,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                },
            )
        else:
            state.consecutive_failures = 0


# Web UIルート（管理者向けダッシュボード）
@app.route("/")
def index():
    """Redirect to admin dashboard."""
    return redirect(url_for("admin_dashboard"))


@app.route("/admin")
@require_admin_auth
def admin_dashboard():
    """Display admin dashboard with agent status."""
    pending_agents = Agent.query.filter(Agent.status == "pending").all()
    approved_agents = Agent.query.filter(Agent.status == "approved").all()
    hold_agents = Agent.query.filter(Agent.status == "hold").all()
    return render_template(
        "admin_dashboard.html",
        pending_agents=pending_agents,
        approved_agents=approved_agents,
        hold_agents=hold_agents,
        current_targets=current_targets,
    )


@app.route("/admin/approve/<int:agent_db_id>", methods=["POST"])
@require_admin_auth
def approve_agent(agent_db_id):
    """Approve a pending agent."""
    agent = Agent.query.get(agent_db_id)
    if agent:
        agent.status = "approved"
        if not agent.agent_id:
            agent.agent_id = f"agent_{agent.id}"
        db.session.commit()
        # Push the effective target list for this agent (template or global).
        socketio.emit(
            "server_message",
            {"type": "update_targets", "targets": _get_targets_for_agent(agent)},
            namespace="/agent",
        )
        return jsonify({"message": "Agent approved", "agent_id": agent.agent_id})
    return jsonify({"error": "Agent not found"}), 404


@app.route("/admin/reject/<int:agent_db_id>", methods=["POST"])
@require_admin_auth
def reject_agent(agent_db_id):
    """Reject and blacklist an agent."""
    agent = Agent.query.get(agent_db_id)
    if agent:
        agent.status = "blacklisted"
        db.session.commit()
        return jsonify({"message": "Agent rejected and blacklisted"})
    return jsonify({"error": "Agent not found"}), 404


# 管理者用: 監視対象リスト更新API
@app.route("/admin/update_targets", methods=["POST"])
@require_admin_auth
def update_targets():
    """Update the global list of monitoring targets via API.

    Agents that have a template assigned are not affected; they continue to
    receive their template-specific target list.
    """
    global current_targets  # pylint: disable=global-statement
    payload = request.get_json(silent=True)
    if payload is None or "targets" not in payload:
        return jsonify({"error": "No targets provided"}), 400
    new_targets = payload.get("targets")
    if not isinstance(new_targets, list) or not all(
        isinstance(target, str) for target in new_targets
    ):
        return jsonify({"error": "Targets must be a list of strings"}), 400
    current_targets = new_targets
    # Broadcast only to agents without a template assignment.
    socketio.emit(
        "server_message",
        {"type": "update_targets", "targets": current_targets},
        namespace="/agent",
    )
    return jsonify({"message": "Targets updated", "targets": current_targets})


@app.route("/admin/update_policy", methods=["GET"])
@require_admin_auth
def get_update_policy():
    """Return the latest configured agent update policy."""
    policy = _get_latest_update_policy()
    if not policy:
        return jsonify(
            {
                "target_version": None,
                "download_url": None,
                "mandatory": False,
                "release_notes": None,
                "created_at": None,
            }
        )
    return jsonify(_build_update_payload(policy))


@app.route("/admin/update_policy", methods=["POST"])
@require_admin_auth
def set_update_policy():
    """Create a new agent update policy and notify agents."""
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Missing JSON body"}), 400

    target_version = (payload.get("target_version") or "").strip()
    download_url = (payload.get("download_url") or "").strip()
    mandatory = payload.get("mandatory", False)
    release_notes = payload.get("release_notes") or None

    if not target_version:
        return jsonify({"error": "target_version is required"}), 400
    if not download_url:
        return jsonify({"error": "download_url is required"}), 400
    if not isinstance(mandatory, bool):
        return jsonify({"error": "mandatory must be a boolean"}), 400
    if release_notes is not None and not isinstance(release_notes, str):
        return jsonify({"error": "release_notes must be a string"}), 400

    policy = AgentUpdatePolicy(
        target_version=target_version,
        download_url=download_url,
        mandatory=mandatory,
        release_notes=release_notes,
    )
    db.session.add(policy)
    db.session.commit()

    payload = _build_update_payload(policy)
    socketio.emit("server_message", payload, namespace="/agent")
    return jsonify(payload), 201


@app.route("/admin/agent_versions", methods=["GET"])
@require_admin_auth
def list_agent_versions():
    """Return agent version tracking data for update management."""
    policy = _get_latest_update_policy()
    agents = Agent.query.order_by(Agent.id).all()
    return jsonify(
        [
            {
                "id": agent.id,
                "agent_id": agent.agent_id,
                "hostname": agent.hostname,
                "ip_address": agent.ip_address,
                "version": agent.version,
                "status": agent.status,
                "last_seen_at": (
                    agent.last_seen_at.isoformat() if agent.last_seen_at else None
                ),
                "update_required": _should_notify_update(agent.version, policy),
                "target_version": policy.target_version if policy else None,
            }
            for agent in agents
        ]
    )


# 管理画面での監視対象リスト管理機能を追加


@app.route("/admin/targets", methods=["GET"])
@require_admin_auth
def manage_targets():
    """Display targets management page."""
    # 管理画面用に監視対象リストの編集フォームを表示
    return render_template("manage_targets.html", current_targets=current_targets)


@app.route("/admin/targets", methods=["POST"])
@require_admin_auth
def update_targets_list():
    """Update the global list of monitoring targets via form submission.

    Agents with a template assigned are not affected; they keep their
    template-specific target list.
    """
    global current_targets  # pylint: disable=global-statement
    # フォームの入力値はカンマ区切りのIPアドレス
    new_targets = request.form.get("targets")
    if new_targets:
        current_targets = [ip.strip() for ip in new_targets.split(",") if ip.strip()]
        # Broadcast global targets; template-assigned agents use their own lists.
        socketio.emit(
            "server_message",
            {"type": "update_targets", "targets": current_targets},
            namespace="/agent",
        )
        return redirect(url_for("manage_targets"))
    return "No targets provided", 400


def is_valid_agent_passphrase(passphrase: str) -> bool:
    """
    Return True if the supplied passphrase is valid for the current AGENT_SECRET config.

    When AGENT_SECRET is not configured (empty string), all passphrases are accepted
    to preserve backward compatibility.  When it is configured, a constant-time
    comparison is used to prevent timing-based side-channel attacks.
    """
    if not AGENT_SECRET:
        return True
    return secrets.compare_digest(passphrase or "", AGENT_SECRET)


# WebSocketネームスペース '/agent' でエージェントとの通信を実施
@socketio.on("connect", namespace="/agent")
def on_connect():
    """Handle agent connection."""
    print("エージェントが接続しました")
    emit("welcome", {"message": "Meshpingサーバに接続しました"})


@socketio.on("handshake", namespace="/agent")
def handle_handshake(data):
    """
    エージェントからの初回ハンドシェイクメッセージを処理します。
    期待フィールド:
      - passphrase: サーバ発行パスフレーズ
      - hostname, ip_address, version: エージェント情報
    """
    passphrase = data.get("passphrase")
    hostname = data.get("hostname")
    ip_address = data.get("ip_address")
    version = data.get("version")
    now = datetime.datetime.utcnow()

    # Validate agent secret when configured.
    if not is_valid_agent_passphrase(passphrase or ""):
        emit(
            "registration_status",
            {"status": "rejected", "message": "Invalid credentials. Connection refused."},
        )
        return

    agent = Agent.query.filter_by(passphrase=passphrase).first()
    if not agent:
        # 新規エージェントの仮登録
        agent = Agent(
            hostname=hostname,
            ip_address=ip_address,
            version=version,
            passphrase=passphrase,
            status="pending",
            last_seen_at=now,
        )
        db.session.add(agent)
        db.session.commit()
        emit(
            "registration_status",
            {"status": "pending", "message": "仮登録完了。管理者承認待ちです。"},
        )
    else:
        # 再接続時または再登録時の処理
        if version:
            agent.version = version
        agent.last_seen_at = now
        if agent.ip_address != ip_address and agent.status == "approved":
            agent.status = "hold"
            db.session.commit()
            emit(
                "registration_status",
                {"status": "hold", "message": "IPアドレス変更: 再承認が必要です。"},
            )
        else:
            db.session.commit()
            emit(
                "registration_status",
                {"status": agent.status, "message": "再接続されました。"},
            )
    # 承認済みの場合は、監視対象リストをプッシュする
    if agent.status == "approved":
        emit(
            "server_message",
            {"type": "update_targets", "targets": _get_targets_for_agent(agent)},
        )
        policy = _get_latest_update_policy()
        if _should_notify_update(agent.version, policy):
            emit("server_message", _build_update_payload(policy))


@socketio.on("monitoring_data", namespace="/agent")
def handle_monitoring_data(data):
    """
    エージェントから5秒毎に送信される監視データを処理します。
    例:
      {
          'agent_id': 'agent_1',
          'data': [
              {
                  'target': '8.8.8.8', 'timestamp': '2025-02-15T12:00:00',
                  'result': 'ok', 'latency': 12.3
              },
              {
                  'target': '1.1.1.1', 'timestamp': '2025-02-15T12:00:00',
                  'result': 'fail', 'latency': 0
              }
          ]
      }
    """
    agent_id = data.get("agent_id")
    entries = data.get("data", [])
    for entry in entries:
        ts = datetime.datetime.fromisoformat(entry["timestamp"])
        mdata = MonitoringData(
            agent_id=agent_id,
            target=entry["target"],
            timestamp=ts,
            result=entry["result"],
            latency=entry.get("latency", 0),
            jitter=entry.get("jitter", 0.0),
            packet_loss=entry.get("packet_loss", 0.0),
        )
        db.session.add(mdata)
        # キャッシュ更新（直近1時間分保持）
        if agent_id not in recent_cache:
            recent_cache[agent_id] = []
        recent_cache[agent_id].append(mdata)
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        recent_cache[agent_id] = [
            d for d in recent_cache[agent_id] if d.timestamp >= cutoff
        ]
        # Evaluate alert conditions for this entry.
        evaluate_alert(
            agent_id,
            entry["target"],
            entry["result"],
            entry.get("latency", 0),
        )
    db.session.commit()
    purge_old_monitoring_data()
    emit("data_received", {"message": "監視データ保存完了"})


# API: マウスオーバー用に直近1時間の監視データ（線グラフ用）を取得する
@app.route("/monitoring/<agent_id>/<target>")
def get_monitoring_data(agent_id, target):
    """Retrieve monitoring data for a specific agent and target."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    mem_data = []
    if agent_id in recent_cache:
        mem_data = [d for d in recent_cache[agent_id] if d.target == target]
    if not mem_data:
        mem_data = MonitoringData.query.filter(
            MonitoringData.agent_id == agent_id,
            MonitoringData.target == target,
            MonitoringData.timestamp >= cutoff,
        ).all()
    response = [
        {
            "timestamp": d.timestamp.isoformat(),
            "latency": d.latency if d.result == "ok" else 0,
            "jitter": d.jitter or 0.0,
            "packet_loss": d.packet_loss or 0.0,
        }
        for d in mem_data
    ]
    return jsonify(response)


@app.route("/monitoring/<agent_id>/<target>/analytics")
def get_monitoring_analytics(agent_id, target):
    """Return hourly-aggregated monitoring statistics for the retention window.

    Each bucket covers one calendar hour and includes:
    - ``hour``: ISO-8601 timestamp of the bucket start (UTC)
    - ``avg_latency``: average RTT (ms) across successful pings; null if no successes
    - ``avg_jitter``: average jitter (ms) across all samples
    - ``avg_packet_loss``: average packet-loss (%) across all samples
    - ``total_samples``: total number of monitoring entries in the bucket
    - ``success_count``: number of entries where result == "ok"
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=RETENTION_HOURS)
    rows = MonitoringData.query.filter(
        MonitoringData.agent_id == agent_id,
        MonitoringData.target == target,
        MonitoringData.timestamp >= cutoff,
    ).order_by(MonitoringData.timestamp).all()

    # Group rows into hourly buckets keyed by (year, month, day, hour).
    buckets: dict = {}
    for row in rows:
        bucket_key = row.timestamp.replace(minute=0, second=0, microsecond=0)
        if bucket_key not in buckets:
            buckets[bucket_key] = {"latencies": [], "jitters": [], "losses": [], "total": 0}
        b = buckets[bucket_key]
        b["total"] += 1
        if row.result == "ok":
            b["latencies"].append(row.latency or 0.0)
        b["jitters"].append(row.jitter or 0.0)
        b["losses"].append(row.packet_loss or 0.0)

    result = []
    for hour_ts in sorted(buckets.keys()):
        b = buckets[hour_ts]
        avg_latency = (sum(b["latencies"]) / len(b["latencies"])) if b["latencies"] else None
        avg_jitter = (sum(b["jitters"]) / len(b["jitters"])) if b["jitters"] else None
        avg_loss = (sum(b["losses"]) / len(b["losses"])) if b["losses"] else None
        result.append(
            {
                "hour": hour_ts.isoformat(),
                "avg_latency": avg_latency,
                "avg_jitter": avg_jitter,
                "avg_packet_loss": avg_loss,
                "total_samples": b["total"],
                "success_count": len(b["latencies"]),
            }
        )
    return jsonify(result)


# ── Alert Rules Management API ──────────────────────────────────────────────


@app.route("/admin/alert_rules", methods=["GET"])
@require_admin_auth
def list_alert_rules():
    """Return all configured alert rules as JSON."""
    rules = AlertRule.query.order_by(AlertRule.id).all()
    return jsonify(
        [
            {
                "id": r.id,
                "target": r.target,
                "consecutive_failures": r.consecutive_failures,
                "latency_threshold_ms": r.latency_threshold_ms,
                "webhook_url": r.webhook_url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rules
        ]
    )


@app.route("/admin/alert_rules", methods=["POST"])
@require_admin_auth
def create_alert_rule():
    """Create a new alert rule.

    Expected JSON body::

        {
            "target": "8.8.8.8",          # required; use "*" for all targets
            "consecutive_failures": 3,     # optional, default 3
            "latency_threshold_ms": 200,   # optional, default 0 (disabled)
            "webhook_url": "https://..."   # optional; overrides ALERT_WEBHOOK_URL
        }
    """
    payload = request.get_json(silent=True)
    if payload is None or "target" not in payload:
        return jsonify({"error": "Missing required field: target"}), 400

    target = payload.get("target", "").strip()
    if not target:
        return jsonify({"error": "target must be a non-empty string"}), 400

    consecutive_failures = payload.get("consecutive_failures", 3)
    latency_threshold_ms = payload.get("latency_threshold_ms", 0.0)
    webhook_url = payload.get("webhook_url") or None

    if not isinstance(consecutive_failures, int) or consecutive_failures < 1:
        return jsonify({"error": "consecutive_failures must be a positive integer"}), 400
    if not isinstance(latency_threshold_ms, (int, float)) or latency_threshold_ms < 0:
        return jsonify({"error": "latency_threshold_ms must be a non-negative number"}), 400

    rule = AlertRule(
        target=target,
        consecutive_failures=consecutive_failures,
        latency_threshold_ms=float(latency_threshold_ms),
        webhook_url=webhook_url,
    )
    db.session.add(rule)
    db.session.commit()
    return (
        jsonify(
            {
                "id": rule.id,
                "target": rule.target,
                "consecutive_failures": rule.consecutive_failures,
                "latency_threshold_ms": rule.latency_threshold_ms,
                "webhook_url": rule.webhook_url,
            }
        ),
        201,
    )


@app.route("/admin/alert_rules/<int:rule_id>", methods=["DELETE"])
@require_admin_auth
def delete_alert_rule(rule_id):
    """Delete an alert rule by ID."""
    rule = AlertRule.query.get(rule_id)
    if not rule:
        return jsonify({"error": "Alert rule not found"}), 404
    db.session.delete(rule)
    db.session.commit()
    return jsonify({"message": "Alert rule deleted"})


@app.route("/admin/alert_states", methods=["GET"])
@require_admin_auth
def list_alert_states():
    """Return current alert states for all agent+target pairs."""
    states = AlertState.query.order_by(AlertState.agent_id, AlertState.target).all()
    return jsonify(
        [
            {
                "id": s.id,
                "agent_id": s.agent_id,
                "target": s.target,
                "consecutive_failures": s.consecutive_failures,
                "status": s.status,
                "last_alert_sent": (
                    s.last_alert_sent.isoformat() if s.last_alert_sent else None
                ),
            }
            for s in states
        ]
    )


# ── Monitoring Templates API ────────────────────────────────────────────────


def _template_to_dict(tmpl: MonitoringTemplate) -> dict:
    """Serialise a MonitoringTemplate to a plain dictionary."""
    return {
        "id": tmpl.id,
        "name": tmpl.name,
        "targets": tmpl.targets,
        "created_at": tmpl.created_at.isoformat() if tmpl.created_at else None,
        "updated_at": tmpl.updated_at.isoformat() if tmpl.updated_at else None,
    }


@app.route("/admin/templates", methods=["GET"])
@require_admin_auth
def list_templates():
    """Return all monitoring templates as JSON."""
    templates = MonitoringTemplate.query.order_by(MonitoringTemplate.id).all()
    return jsonify([_template_to_dict(t) for t in templates])


@app.route("/admin/templates", methods=["POST"])
@require_admin_auth
def create_template():
    """Create a new monitoring template.

    Expected JSON body::

        {
            "name": "customer-A",          # required; must be unique
            "targets": ["10.0.0.1", ...]   # required; list of IP/hostname strings
        }
    """
    payload = request.get_json(silent=True)
    if payload is None or "name" not in payload or "targets" not in payload:
        return jsonify({"error": "Missing required fields: name, targets"}), 400

    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name must be a non-empty string"}), 400

    targets_list = payload.get("targets")
    if not isinstance(targets_list, list) or not all(
        isinstance(t, str) for t in targets_list
    ):
        return jsonify({"error": "targets must be a list of strings"}), 400

    if MonitoringTemplate.query.filter_by(name=name).first():
        return jsonify({"error": "A template with that name already exists"}), 409

    tmpl = MonitoringTemplate(name=name)
    tmpl.targets = targets_list
    db.session.add(tmpl)
    db.session.commit()
    return jsonify(_template_to_dict(tmpl)), 201


@app.route("/admin/templates/<int:template_id>", methods=["GET"])
@require_admin_auth
def get_template(template_id):
    """Return a single monitoring template by ID."""
    tmpl = MonitoringTemplate.query.get(template_id)
    if not tmpl:
        return jsonify({"error": "Template not found"}), 404
    return jsonify(_template_to_dict(tmpl))


@app.route("/admin/templates/<int:template_id>", methods=["PUT"])
@require_admin_auth
def update_template(template_id):
    """Update a monitoring template.

    Accepts a JSON body with optional ``name`` and/or ``targets`` fields.
    After saving, pushes the new target list to all agents assigned to this
    template.
    """
    tmpl = MonitoringTemplate.query.get(template_id)
    if not tmpl:
        return jsonify({"error": "Template not found"}), 404

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Missing JSON body"}), 400

    if "name" in payload:
        new_name = (payload["name"] or "").strip()
        if not new_name:
            return jsonify({"error": "name must be a non-empty string"}), 400
        existing = MonitoringTemplate.query.filter_by(name=new_name).first()
        if existing and existing.id != template_id:
            return jsonify({"error": "A template with that name already exists"}), 409
        tmpl.name = new_name

    if "targets" in payload:
        targets_list = payload["targets"]
        if not isinstance(targets_list, list) or not all(
            isinstance(t, str) for t in targets_list
        ):
            return jsonify({"error": "targets must be a list of strings"}), 400
        tmpl.targets = targets_list

    db.session.commit()

    # Note: the current architecture uses a namespace-wide broadcast; there is
    # no per-agent session tracking in admin endpoints.  We therefore only emit
    # when at least one approved agent is bound to this template, sending the
    # updated target list as a broadcast.  Agents that are not assigned to this
    # template will also receive the message but will be corrected on their next
    # reconnect or the next global target push.
    has_approved = any(a.status == "approved" for a in tmpl.agents)
    if has_approved:
        socketio.emit(
            "server_message",
            {"type": "update_targets", "targets": tmpl.targets},
            namespace="/agent",
        )

    return jsonify(_template_to_dict(tmpl))


@app.route("/admin/templates/<int:template_id>", methods=["DELETE"])
@require_admin_auth
def delete_template(template_id):
    """Delete a monitoring template.

    Agents assigned to this template will have their ``template_id`` cleared
    and will fall back to the global target list.
    """
    tmpl = MonitoringTemplate.query.get(template_id)
    if not tmpl:
        return jsonify({"error": "Template not found"}), 404

    # Detach agents before deleting the template.
    for agent in list(tmpl.agents):
        agent.template_id = None

    db.session.delete(tmpl)
    db.session.commit()
    return jsonify({"message": "Template deleted"})


@app.route("/admin/agents/<int:agent_db_id>/assign_template", methods=["POST"])
@require_admin_auth
def assign_template_to_agent(agent_db_id):
    """Assign (or unassign) a monitoring template to an agent.

    Expected JSON body::

        {"template_id": 3}   # assign template with ID 3
        {"template_id": null} # unassign — agent falls back to global targets
    """
    agent = Agent.query.get(agent_db_id)
    if not agent:
        return jsonify({"error": "Agent not found"}), 404

    payload = request.get_json(silent=True)
    if payload is None or "template_id" not in payload:
        return jsonify({"error": "Missing required field: template_id"}), 400

    tmpl_id = payload["template_id"]
    if tmpl_id is None:
        agent.template_id = None
    else:
        if not isinstance(tmpl_id, int):
            return jsonify({"error": "template_id must be an integer or null"}), 400
        tmpl = MonitoringTemplate.query.get(tmpl_id)
        if not tmpl:
            return jsonify({"error": "Template not found"}), 404
        agent.template_id = tmpl_id

    db.session.commit()

    # Broadcast the effective target list so the newly-assigned agent picks it
    # up immediately.  The current architecture has no per-agent session rooms,
    # so this is a namespace-wide broadcast; other connected agents will also
    # receive the message and will be corrected on their next global push or
    # reconnect.
    if agent.status == "approved":
        socketio.emit(
            "server_message",
            {"type": "update_targets", "targets": _get_targets_for_agent(agent)},
            namespace="/agent",
        )

    return jsonify(
        {
            "message": "Template assignment updated",
            "agent_id": agent.agent_id,
            "template_id": agent.template_id,
        }
    )


if __name__ == "__main__":
    if not os.path.exists("meshping.db"):
        db.create_all()
    # SSL 証明書と秘密鍵のパスを設定（運用環境用に適切なパスに変更してください）
    ssl_context = ("/workspaces/meshping/cert.pem", "/workspaces/meshping/key.pem")
    socketio.run(app, host="0.0.0.0", port=5000, ssl_context=ssl_context)
