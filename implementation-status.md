# 実装状況レポート（PoCギャップ整理）

## 1. PoCで実装済みのコア挙動（server.py / agent.go）

### エージェントハンドシェイク・登録
- server.py: `/agent` namespace の `handshake` イベントで passphrase/hostname/ip_address/version を受け取り、未登録なら pending で仮登録。既存エージェントで IP が変わった場合は hold へ変更し、登録状態を返信する。
- agent.go: 起動時に handshake を送信し、返却された registration_status が pending/hold の場合は終了。

### 承認フロー
- server.py: 管理画面から承認時に status=approved とし agent_id を付与。承認済みエージェントへ監視対象リストを push。
- server.py: 拒否時は status=blacklisted に更新。

### 監視対象リストの配信（ターゲットプッシュ）
- server.py: 管理API・管理フォームから current_targets を更新し、全エージェントへ update_targets を push。
- agent.go: update_targets を受信して targets を更新し、初回更新で待機解除。

### ICMP監視と送信
- agent.go: 5秒ごとに全ターゲットへ ICMP Echo を送信（並行処理）。成功時の RTT をミリ秒で記録。
- agent.go: 監視結果を MonitoringDataMessage として WebSocket 経由で送信。

### データ保存
- server.py: monitoring_data を受信したら SQLite に保存。

### 1時間キャッシュ
- server.py: recent_cache に直近1時間分の MonitoringData を保持し、/monitoring/<agent>/<target> で返却。

## 2. specs.txt との未実装・部分実装チェック

### 未実装
- ユーザ管理（登録/権限/ログイン/パスワードリセット）
- リアルタイム監視画面（行×列の秒単位UI、色分け）
- パスフレーズ生成・暗号化交換（新規登録画面含む）
- 24時間のデータリテンション（DB保持/削除ポリシー）
- エージェント側の30分バッファ（TTL管理）

### 部分実装
- ホバー表示: 1時間分データ取得 API は存在するが、UI 実装は未確認/未実装
- TLS: server.py は SSL context を指定するが、agent.go は ws:// を使用

## 3. Baseline expectations vs. reality（将来 issue 用メモ）

- 期待: TLSでのWebSocket通信 → 実態: サーバはSSL設定あり、エージェントは ws://
- 期待: リアルタイム監視UI/ホバーUI → 実態: APIは一部あり、UIは未実装
- 期待: 24時間データ保持 → 実態: 1時間キャッシュのみ実装、DB保持は未実装

## 4. ギャップ追跡のチェックリスト（Living）

### Server
- [x] ハンドシェイク/仮登録/保留処理
- [x] 管理承認/拒否
- [x] 監視対象リスト push
- [x] 監視データ保存 + 1時間キャッシュ
- [ ] ユーザ管理
- [ ] リアルタイム監視UI
- [ ] ホバーUI
- [ ] パスフレーズ暗号化/登録画面
- [ ] 24時間リテンション
- [ ] TLS (agent側 wss 対応)

### Agent
- [x] ICMP監視（5秒間隔・並行）
- [x] 監視対象の更新受信
- [x] 送信失敗バッファ
- [ ] 30分バッファ（TTL管理）
