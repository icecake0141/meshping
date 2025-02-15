# server.py のコード実装解説

このドキュメントは、server.py 内の主要なコードブロックごとに、その処理内容と役割を詳細に解説したものです。  
本更新では、以下の新機能を追加しています:  
- パスフレーズ交換方式によるエージェントの仮登録と承認プロセス  
- 管理画面での監視対象リスト（IP）の編集、追加、削除機能（更新時は全エージェントへプッシュ配信）

---

## 1. Flask アプリケーションの初期設定と基本設定

```python
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'replace_with_a_secure_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///meshping.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app)
```

**解説:**  
- Flask アプリケーションとその基本設定（セキュリティ、データベース接続）を行っています。  
- SocketIO を使用して WebSocket 通信の基盤を構築しています。

---

## 2. DB モデルの定義とエージェント登録情報の管理

```python
# DB Models
class Agent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.String(64), unique=True, nullable=True)
    hostname = db.Column(db.String(128))
    ip_address = db.Column(db.String(64))
    version = db.Column(db.String(32))
    passphrase = db.Column(db.String(128))
    status = db.Column(db.String(32), default="pending")  # 状態: pending, approved, hold, blacklisted

    registered_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class MonitoringData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.String(64))  # エージェントID
    target = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime)
    result = db.Column(db.String(16))  # "ok" または "fail"
    latency = db.Column(db.Float)  # RTT（成功時） or 0
```

**解説:**  
- **Agent** クラスは、パスフレーズ交換方式によりエージェントの仮登録情報を保存します。  
- エージェントは、パスフレーズを用いて登録・認証され、管理者による承認後に正式な運用状態（approved）となります。  
- **MonitoringData** クラスは、監視データを保存するためのテーブルです。

---

## 3. WebSocket 通信とエージェントハンドシェイク処理

```python
@socketio.on('connect', namespace='/agent')
def on_connect():
    print("エージェントが接続しました")
    emit('welcome', {'message': 'Meshpingサーバに接続しました'})

@socketio.on('handshake', namespace='/agent')
def handle_handshake(data):
    """
    エージェントからの初回ハンドシェイクメッセージの処理。  
    パスフレーズを含むデータにより、仮登録を実施します。
    """
    passphrase = data.get('passphrase')
    hostname = data.get('hostname')
    ip_address = data.get('ip_address')
    version = data.get('version')

    # パスフレーズ交換方式による仮登録処理
    agent = Agent.query.filter_by(passphrase=passphrase).first()
    if not agent:
        agent = Agent(hostname=hostname, ip_address=ip_address, version=version,
                      passphrase=passphrase, status='pending')
        db.session.add(agent)
        db.session.commit()
        emit('registration_status', {'status': 'pending',
                                     'message': '仮登録完了。管理者承認待ちです。'})
    else:
        # 再接続またはIPアドレス変更時の処理
        if agent.ip_address != ip_address and agent.status == 'approved':
            agent.status = 'hold'
            db.session.commit()
            emit('registration_status', {'status': 'hold',
                                         'message': 'IPアドレス変更のため再承認が必要です。'})
        else:
            emit('registration_status', {'status': agent.status,
                                         'message': '再接続されました。'})
    # 承認済みの場合には、最新の監視対象リストをプッシュ配信
    if agent.status == 'approved':
        emit('server_message', {'type': 'update_targets', 'targets': current_targets})
```

**解説:**  
- エージェントからの接続時にウェルカムメッセージを返します。  
- `handle_handshake` 関数は、エージェントから受信するパスフレーズ等の情報に基づいて仮登録を実施し、再接続時やIP変更時の対応も行います。  
- 承認済みであれば、最新の監視対象リスト（current_targets）を送信します。

---

## 4. REST API と管理ダッシュボード機能

```python
@app.route('/admin')
def admin_dashboard():
    pending_agents = Agent.query.filter(Agent.status == 'pending').all()
    approved_agents = Agent.query.filter(Agent.status == 'approved').all()
    hold_agents = Agent.query.filter(Agent.status == 'hold').all()
    return render_template('admin_dashboard.html',
                           pending_agents=pending_agents,
                           approved_agents=approved_agents,
                           hold_agents=hold_agents,
                           current_targets=current_targets)

# 監視対象リスト管理画面（編集、追加、削除）機能
@app.route('/admin/targets', methods=['GET'])
def manage_targets():
    return render_template('manage_targets.html', current_targets=current_targets)

@app.route('/admin/targets', methods=['POST'])
def update_targets_list():
    global current_targets
    new_targets = request.form.get('targets')
    if new_targets:
        current_targets = [ip.strip() for ip in new_targets.split(',') if ip.strip()]
        # 監視対象リスト更新後、全エージェントへプッシュ配信
        socketio.emit('server_message', {'type': 'update_targets', 'targets': current_targets},
                      namespace='/agent')
        return redirect(url_for('manage_targets'))
    else:
        return "No targets provided", 400
```

**解説:**  
- `admin_dashboard` では、エージェントの状態別リストを表示し、管理操作のためのダッシュボードを提供します。  
- `/admin/targets` エンドポイントにより、管理者は監視対象IPリストの編集、追加、削除が可能になり、更新時は即座に全エージェントへ新リストがプッシュされます。

---

以上が server.py の主な実装部分の解説です。  
新規機能として、パスフレーズ交換方式によるエージェント登録および管理画面での監視対象リスト編集機能が実装されています。