// Copyright 2026 Meshping Contributors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// NOTE: This file may include code that was generated or suggested by a large language model (LLM).

package main

import (
	"encoding/json"
	"log"
	"net"
	"os"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"golang.org/x/net/icmp"
	"golang.org/x/net/ipv4"
)

const (
	// サーバのWebSocketエンドポイント（必要に応じて変更）
	serverURL        = "ws://localhost:5000/agent"
	passphrase       = "your_passphrase" // 管理サーバで発行されたパスフレーズ
	version          = "1.0.0"
	pingTimeout      = 3 * time.Second
	icmpProtocolICMP = 1
	echoData         = "HELLO-R-U-THERE"
)

var (
	// 監視対象はサーバ側で一元管理するため、更新されたリストを格納する。
	targets      []string
	targetsMutex sync.RWMutex
	// 初回監視対象リスト受信完了を待つためのチャネル
	initialTargetsReceived = make(chan bool, 1)
)

// HandshakeMessage は初回接続時に送信するメッセージです。
type HandshakeMessage struct {
	Passphrase string `json:"passphrase"`
	Hostname   string `json:"hostname"`
	IPAddress  string `json:"ip_address"`
	Version    string `json:"version"`
}

// RegistrationStatus はサーバから返される認証状態のメッセージです。
type RegistrationStatus struct {
	Status  string `json:"status"`
	Message string `json:"message"`
	AgentID string `json:"agent_id,omitempty"`
}

// ServerMessage はサーバ側からの各種プッシュメッセージを表します。
type ServerMessage struct {
	Type    string   `json:"type"`
	Targets []string `json:"targets,omitempty"`
}

// MonitoringEntry は各監視対象の結果を表現します。
type MonitoringEntry struct {
	Target    string  `json:"target"`
	Timestamp string  `json:"timestamp"`
	Result    string  `json:"result"` // "ok" または "fail"
	Latency   float64 `json:"latency"`
}

// MonitoringDataMessage は5秒毎に送信する監視データのメッセージです。
type MonitoringDataMessage struct {
	AgentID string            `json:"agent_id"`
	Data    []MonitoringEntry `json:"data"`
}

// PingResult は各対象のping結果を保持します。
type PingResult struct {
	Target  string
	Ok      bool
	Latency float64
}

// getHostname はローカルホスト名を取得します。
func getHostname() string {
	hostname, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return hostname
}

// getLocalIP は簡易的にローカルIPアドレスを取得します。
func getLocalIP() string {
	conn, err := net.Dial("udp", "8.8.8.8:80")
	if err != nil {
		return "0.0.0.0"
	}
	defer conn.Close()
	localAddr := conn.LocalAddr().(*net.UDPAddr)
	return localAddr.IP.String()
}

// pingTargetInternal はgolang.org/x/net/icmpパッケージを使用してICMP Echoを送信します。
func pingTargetInternal(target string) (bool, float64) {
	c, err := icmp.ListenPacket("ip4:icmp", "0.0.0.0")
	if err != nil {
		log.Println("ICMPリスンエラー:", err)
		return false, 0
	}
	defer c.Close()

	wm := icmp.Message{
		Type: ipv4.ICMPTypeEcho,
		Code: 0,
		Body: &icmp.Echo{
			ID:   os.Getpid() & 0xffff,
			Seq:  1,
			Data: []byte(echoData),
		},
	}
	wb, err := wm.Marshal(nil)
	if err != nil {
		log.Println("ICMPメッセージマーシャリングエラー:", err)
		return false, 0
	}
	dst, err := net.ResolveIPAddr("ip4", target)
	if err != nil {
		log.Println("IP解決エラー:", err)
		return false, 0
	}
	start := time.Now()
	if _, err = c.WriteTo(wb, dst); err != nil {
		log.Println("ICMP送信エラー:", err)
		return false, 0
	}

	err = c.SetReadDeadline(time.Now().Add(pingTimeout))
	if err != nil {
		log.Println("SetReadDeadlineエラー:", err)
		return false, 0
	}

	rb := make([]byte, 1500)
	n, peer, err := c.ReadFrom(rb)
	if err != nil {
		log.Println("ICMP受信エラー:", err)
		return false, 0
	}
	duration := time.Since(start)
	rm, err := icmp.ParseMessage(icmpProtocolICMP, rb[:n])
	if err != nil {
		log.Println("ICMPメッセージ解析エラー:", err)
		return false, 0
	}
	switch rm.Type {
	case ipv4.ICMPTypeEchoReply:
		if peer.String() == dst.String() {
			return true, duration.Seconds() * 1000.0 // ミリ秒換算
		}
		return true, duration.Seconds() * 1000.0
	default:
		return false, 0
	}
}

var pingTarget = pingTargetInternal

// pingAllTargets concurrently pings all registered targets.
func pingAllTargets() []PingResult {
	targetsMutex.RLock()
	currentTargets := make([]string, len(targets))
	copy(currentTargets, targets)
	targetsMutex.RUnlock()

	var wg sync.WaitGroup
	results := make([]PingResult, len(currentTargets))
	for i, target := range currentTargets {
		wg.Add(1)
		go func(i int, target string) {
			defer wg.Done()
			ok, latency := pingTarget(target)
			results[i] = PingResult{
				Target:  target,
				Ok:      ok,
				Latency: latency,
			}
		}(i, target)
	}
	wg.Wait()
	return results
}

func main() {
	// WebSocketサーバに接続
	ws, _, err := websocket.DefaultDialer.Dial(serverURL, nil)
	if err != nil {
		log.Fatal("WebSocket接続エラー:", err)
	}
	defer ws.Close()

	// ハンドシェイク：エージェント情報を送信
	hostname := getHostname()
	ipAddress := getLocalIP()
	handshake := HandshakeMessage{
		Passphrase: passphrase,
		Hostname:   hostname,
		IPAddress:  ipAddress,
		Version:    version,
	}
	if err := ws.WriteJSON(handshake); err != nil {
		log.Fatal("ハンドシェイク送信エラー:", err)
	}

	// サーバからの認証結果を受信
	var regStatus RegistrationStatus
	if err := ws.ReadJSON(&regStatus); err != nil {
		log.Fatal("認証結果受信エラー:", err)
	}
	log.Printf("認証結果: %s - %s", regStatus.Status, regStatus.Message)
	agentID := regStatus.AgentID
	if regStatus.Status == "pending" || regStatus.Status == "hold" {
		log.Println("エージェントが承認状態ではありません。終了します。")
		return
	}

	// サーバから監視対象リストの更新を受信するゴルーチンを起動
	go func() {
		for {
			var srvMsg ServerMessage
			if err := ws.ReadJSON(&srvMsg); err != nil {
				log.Println("サーバメッセージ受信エラー:", err)
				time.Sleep(5 * time.Second)
				continue
			}
			switch srvMsg.Type {
			case "update_targets":
				targetsMutex.Lock()
				targets = srvMsg.Targets
				targetsMutex.Unlock()
				log.Println("監視対象リスト更新:", srvMsg.Targets)
				// 初回更新受信の場合は待機チャネルを解放
				select {
				case initialTargetsReceived <- true:
				default:
				}
			default:
				log.Println("不明なメッセージタイプ:", srvMsg.Type)
			}
		}
	}()

	// 初回監視対象リストを待機（ブロッキング）
	log.Println("初回の監視対象リスト受信待機中...")
	<-initialTargetsReceived
	log.Println("初回監視対象リスト受信完了。")

	// 送信失敗時にためておくバッファ
	unsentBuffer := []MonitoringDataMessage{}

	// 5秒毎に監視データを送信するTicker
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		var entries []MonitoringEntry
		currentTime := time.Now().Format(time.RFC3339)

		// 並行処理で全監視対象へICMP送信
		results := pingAllTargets()
		for _, res := range results {
			entry := MonitoringEntry{
				Target:    res.Target,
				Timestamp: currentTime,
				Result:    "fail",
				Latency:   0,
			}
			if res.Ok {
				entry.Result = "ok"
				entry.Latency = res.Latency
			}
			entries = append(entries, entry)
		}
		message := MonitoringDataMessage{
			AgentID: agentID,
			Data:    entries,
		}

		// 先に未送信のバッファ内メッセージを送信
		for len(unsentBuffer) > 0 {
			if err := ws.WriteJSON(unsentBuffer[0]); err != nil {
				log.Println("バッファ内メッセージ送信失敗。再試行します:", err)
				break
			}
			unsentBuffer = unsentBuffer[1:]
		}

		// 現在の監視データを送信
		if err := ws.WriteJSON(message); err != nil {
			log.Println("監視データ送信失敗。バッファに保持します:", err)
			unsentBuffer = append(unsentBuffer, message)
		} else {
			log.Println("監視データ送信成功:", toJSON(message))
		}
	}
}

// toJSON はメッセージ内容をJSON文字列に変換するヘルパー関数です。
func toJSON(v interface{}) string {
	bytes, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(bytes)
}
