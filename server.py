"""
Meshping server application for monitoring network connectivity.

This module implements a Flask-based server that manages monitoring agents
and collects ping data from distributed agents across the network.
"""
# pylint: disable=import-error
import datetime
import os

from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'replace_with_a_secure_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///meshping.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app)

# Flask アプリケーション設定の直後にグローバル変数を初期化
current_targets = []  # 監視対象IPリストの初期値（空リストで初期化）

# DB Models
class Agent(db.Model):  # pylint: disable=too-few-public-methods
    """Database model for monitoring agents."""
    id = db.Column(db.Integer, primary_key=True)
    # 仮登録時はpassphraseのみが識別子。後にエージェントIDを割り振る。
    agent_id = db.Column(db.String(64), unique=True, nullable=True)
    hostname = db.Column(db.String(128))
    ip_address = db.Column(db.String(64))
    version = db.Column(db.String(32))
    passphrase = db.Column(db.String(128))
    status = db.Column(db.String(32), default="pending")  # pending, approved, hold, blacklisted

    registered_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )

class MonitoringData(db.Model):  # pylint: disable=too-few-public-methods
    """Database model for monitoring data collected from agents."""
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.String(64))  # エージェントID
    target = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime)
    result = db.Column(db.String(16))  # "ok" または "fail"
    latency = db.Column(db.Float)  # RTT（成功時） or 0

# メモリ上のキャッシュ（直近1時間分のデータ保持）
recent_cache = {}  # { agent_id: [MonitoringData, ...] }

# Web UIルート（管理者向けダッシュボード）
@app.route('/')
def index():
    """Redirect to admin dashboard."""
    return redirect(url_for('admin_dashboard'))

@app.route('/admin')
def admin_dashboard():
    """Display admin dashboard with agent status."""
    pending_agents = Agent.query.filter(Agent.status == 'pending').all()
    approved_agents = Agent.query.filter(Agent.status == 'approved').all()
    hold_agents = Agent.query.filter(Agent.status == 'hold').all()
    return render_template('admin_dashboard.html',
                           pending_agents=pending_agents,
                           approved_agents=approved_agents,
                           hold_agents=hold_agents,
                           current_targets=current_targets)

@app.route('/admin/approve/<int:agent_db_id>', methods=['POST'])
def approve_agent(agent_db_id):
    """Approve a pending agent."""
    agent = Agent.query.get(agent_db_id)
    if agent:
        agent.status = 'approved'
        if not agent.agent_id:
            agent.agent_id = f"agent_{agent.id}"
        db.session.commit()
        # 監視対象リストを承認済みエージェントへプッシュする
        socketio.emit('server_message', {'type': 'update_targets', 'targets': current_targets},
                      namespace='/agent')
        return jsonify({'message': 'Agent approved', 'agent_id': agent.agent_id})
    return jsonify({'error': 'Agent not found'}), 404

@app.route('/admin/reject/<int:agent_db_id>', methods=['POST'])
def reject_agent(agent_db_id):
    """Reject and blacklist an agent."""
    agent = Agent.query.get(agent_db_id)
    if agent:
        agent.status = 'blacklisted'
        db.session.commit()
        return jsonify({'message': 'Agent rejected and blacklisted'})
    return jsonify({'error': 'Agent not found'}), 404

# 管理者用: 監視対象リスト更新API
@app.route('/admin/update_targets', methods=['POST'])
def update_targets():
    """Update the list of monitoring targets via API."""
    global current_targets  # pylint: disable=global-statement
    payload = request.get_json(silent=True)
    if payload is None or 'targets' not in payload:
        return jsonify({'error': 'No targets provided'}), 400
    new_targets = payload.get('targets')
    if not isinstance(new_targets, list) or \
            not all(isinstance(target, str) for target in new_targets):
        return jsonify({'error': 'Targets must be a list of strings'}), 400
    current_targets = new_targets
    socketio.emit('server_message', {'type': 'update_targets', 'targets': current_targets},
                  namespace='/agent')
    return jsonify({'message': 'Targets updated', 'targets': current_targets})

# 管理画面での監視対象リスト管理機能を追加

@app.route('/admin/targets', methods=['GET'])
def manage_targets():
    """Display targets management page."""
    # 管理画面用に監視対象リストの編集フォームを表示
    return render_template('manage_targets.html', current_targets=current_targets)

@app.route('/admin/targets', methods=['POST'])
def update_targets_list():
    """Update the list of monitoring targets via form submission."""
    global current_targets  # pylint: disable=global-statement
    # フォームの入力値はカンマ区切りのIPアドレス
    new_targets = request.form.get('targets')
    if new_targets:
        current_targets = [ip.strip() for ip in new_targets.split(',') if ip.strip()]
        # 全エージェントへ新しい監視対象リストをプッシュ
        socketio.emit('server_message', {'type': 'update_targets', 'targets': current_targets},
                      namespace='/agent')
        return redirect(url_for('manage_targets'))
    return "No targets provided", 400

# WebSocketネームスペース '/agent' でエージェントとの通信を実施
@socketio.on('connect', namespace='/agent')
def on_connect():
    """Handle agent connection."""
    print("エージェントが接続しました")
    emit('welcome', {'message': 'Meshpingサーバに接続しました'})

@socketio.on('handshake', namespace='/agent')
def handle_handshake(data):
    """
    エージェントからの初回ハンドシェイクメッセージを処理します。
    期待フィールド:
      - passphrase: サーバ発行パスフレーズ
      - hostname, ip_address, version: エージェント情報
    """
    passphrase = data.get('passphrase')
    hostname = data.get('hostname')
    ip_address = data.get('ip_address')
    version = data.get('version')

    agent = Agent.query.filter_by(passphrase=passphrase).first()
    if not agent:
        # 新規エージェントの仮登録
        agent = Agent(hostname=hostname, ip_address=ip_address, version=version,
                      passphrase=passphrase, status='pending')
        db.session.add(agent)
        db.session.commit()
        emit('registration_status', {
            'status': 'pending',
            'message': '仮登録完了。管理者承認待ちです。'
        })
    else:
        # 再接続時または再登録時の処理
        if agent.ip_address != ip_address and agent.status == 'approved':
            agent.status = 'hold'
            db.session.commit()
            emit('registration_status', {
                'status': 'hold',
                'message': 'IPアドレス変更: 再承認が必要です。'
            })
        else:
            emit('registration_status', {
                'status': agent.status,
                'message': '再接続されました。'
            })
    # 承認済みの場合は、監視対象リストをプッシュする
    if agent.status == 'approved':
        emit('server_message', {'type': 'update_targets', 'targets': current_targets})

@socketio.on('monitoring_data', namespace='/agent')
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
    agent_id = data.get('agent_id')
    entries = data.get('data', [])
    for entry in entries:
        ts = datetime.datetime.fromisoformat(entry['timestamp'])
        mdata = MonitoringData(agent_id=agent_id,
                               target=entry['target'],
                               timestamp=ts,
                               result=entry['result'],
                               latency=entry.get('latency', 0))
        db.session.add(mdata)
        # キャッシュ更新（直近1時間分保持）
        if agent_id not in recent_cache:
            recent_cache[agent_id] = []
        recent_cache[agent_id].append(mdata)
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        recent_cache[agent_id] = [d for d in recent_cache[agent_id] if d.timestamp >= cutoff]
    db.session.commit()
    emit('data_received', {'message': '監視データ保存完了'})

# API: マウスオーバー用に直近1時間の監視データ（線グラフ用）を取得する
@app.route('/monitoring/<agent_id>/<target>')
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
            MonitoringData.timestamp >= cutoff
        ).all()
    response = [{
        'timestamp': d.timestamp.isoformat(),
        'latency': d.latency if d.result == 'ok' else 0
    } for d in mem_data]
    return jsonify(response)

if __name__ == '__main__':
    if not os.path.exists('meshping.db'):
        db.create_all()
    # SSL 証明書と秘密鍵のパスを設定（運用環境用に適切なパスに変更してください）
    ssl_context = ('/workspaces/meshping/cert.pem', '/workspaces/meshping/key.pem')
    socketio.run(app, host='0.0.0.0', port=5000, ssl_context=ssl_context)
