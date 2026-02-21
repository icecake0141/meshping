// Copyright 2026 Meshping Contributors
// SPDX-License-Identifier: Apache-2.0
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
// This file was created or modified with the assistance of an AI (Large Language Model).
// Review required for correctness, security, and licensing.

package main

import (
	"encoding/json"
	"log"
	"math"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"golang.org/x/net/icmp"
	"golang.org/x/net/ipv4"
)

const (
	// サーバのWebSocketエンドポイント（必要に応じて変更）
	serverURL         = "ws://localhost:5000/agent"
	defaultPassphrase = "your_passphrase" // fallback; prefer AGENT_SECRET env var
	version           = "1.0.0"
	pingTimeout       = 3 * time.Second
	icmpProtocolICMP  = 1
	echoData          = "HELLO-R-U-THERE"
	// slaWindowSize is the number of recent ping results kept per target for SLA computation.
	slaWindowSize = 20
)

var (
	// 監視対象はサーバ側で一元管理するため、更新されたリストを格納する。
	targets      []string
	targetsMutex sync.RWMutex
	// 初回監視対象リスト受信完了を待つためのチャネル
	initialTargetsReceived = make(chan bool, 1)

	// targetHistory keeps a rolling window of recent ping outcomes per target.
	targetHistory      = map[string]*targetWindow{}
	targetHistoryMutex sync.Mutex
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
	Type          string   `json:"type"`
	Targets       []string `json:"targets,omitempty"`
	TargetVersion string   `json:"target_version,omitempty"`
	DownloadURL   string   `json:"download_url,omitempty"`
	Mandatory     bool     `json:"mandatory,omitempty"`
	ReleaseNotes  string   `json:"release_notes,omitempty"`
}

// MonitoringEntry は各監視対象の結果を表現します。
type MonitoringEntry struct {
	Target     string  `json:"target"`
	Timestamp  string  `json:"timestamp"`
	Result     string  `json:"result"` // "ok" または "fail"
	Latency    float64 `json:"latency"`
	Jitter     float64 `json:"jitter"`      // mean absolute jitter in ms (rolling window)
	PacketLoss float64 `json:"packet_loss"` // packet loss percentage (rolling window)
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

// targetWindow stores a rolling window of recent ping outcomes for SLA computation.
type targetWindow struct {
	results   []bool    // true=ok, false=fail
	latencies []float64 // latency (ms) per result; 0 for failures
}

// computeSLAMetrics updates the rolling window for target with the latest ping result
// and returns jitter (mean absolute difference between consecutive successful RTTs, ms)
// and packetLoss (percentage of failures in the window).
func computeSLAMetrics(target string, ok bool, latency float64) (jitter, packetLoss float64) {
	targetHistoryMutex.Lock()
	defer targetHistoryMutex.Unlock()

	w, exists := targetHistory[target]
	if !exists {
		w = &targetWindow{}
		targetHistory[target] = w
	}

	w.results = append(w.results, ok)
	w.latencies = append(w.latencies, latency)
	if len(w.results) > slaWindowSize {
		w.results = w.results[1:]
		w.latencies = w.latencies[1:]
	}

	// Packet loss: fraction of failures * 100.
	failed := 0
	for _, r := range w.results {
		if !r {
			failed++
		}
	}
	packetLoss = float64(failed) / float64(len(w.results)) * 100.0

	// Jitter: mean absolute difference between consecutive successful RTTs.
	var successLatencies []float64
	for i, r := range w.results {
		if r {
			successLatencies = append(successLatencies, w.latencies[i])
		}
	}
	if len(successLatencies) >= 2 {
		var sumDiff float64
		for i := 1; i < len(successLatencies); i++ {
			sumDiff += math.Abs(successLatencies[i] - successLatencies[i-1])
		}
		jitter = sumDiff / float64(len(successLatencies)-1)
	}

	return jitter, packetLoss
}

// parseVersionParts converts a dotted version string into integer segments.
func parseVersionParts(version string) []int {
	parts := strings.Split(version, ".")
	values := make([]int, 0, len(parts))
	for _, part := range parts {
		digits := ""
		for _, ch := range part {
			if ch >= '0' && ch <= '9' {
				digits += string(ch)
			} else {
				break
			}
		}
		if digits == "" {
			values = append(values, 0)
			continue
		}
		value, err := strconv.Atoi(digits)
		if err != nil {
			value = 0
		}
		values = append(values, value)
	}
	return values
}

// isVersionBehind returns true when current is behind target.
func isVersionBehind(current, target string) bool {
	if target == "" {
		return false
	}
	if current == "" {
		return true
	}
	currentParts := parseVersionParts(current)
	targetParts := parseVersionParts(target)
	maxLen := len(currentParts)
	if len(targetParts) > maxLen {
		maxLen = len(targetParts)
	}
	for len(currentParts) < maxLen {
		currentParts = append(currentParts, 0)
	}
	for len(targetParts) < maxLen {
		targetParts = append(targetParts, 0)
	}
	for i := 0; i < maxLen; i++ {
		if currentParts[i] < targetParts[i] {
			return true
		}
		if currentParts[i] > targetParts[i] {
			return false
		}
	}
	return false
}

// getPassphrase returns the agent shared secret.
// It reads AGENT_SECRET from the environment; falls back to the compiled default.
func getPassphrase() string {
	if secret := os.Getenv("AGENT_SECRET"); secret != "" {
		return secret
	}
	return defaultPassphrase
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
		Passphrase: getPassphrase(),
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
			case "agent_update":
				if srvMsg.TargetVersion == "" {
					log.Println("更新通知にターゲットバージョンが含まれていません")
					continue
				}
				if !isVersionBehind(version, srvMsg.TargetVersion) {
					log.Printf(
						"更新通知受信済み: 現在バージョン %s (ターゲット %s)",
						version,
						srvMsg.TargetVersion,
					)
					continue
				}
				log.Printf(
					"エージェント更新が必要です: 現在 %s → 目標 %s",
					version,
					srvMsg.TargetVersion,
				)
				if srvMsg.DownloadURL != "" {
					log.Printf("更新用ダウンロードURL: %s", srvMsg.DownloadURL)
				}
				if srvMsg.ReleaseNotes != "" {
					log.Printf("更新内容: %s", srvMsg.ReleaseNotes)
				}
				if srvMsg.Mandatory {
					log.Println("必須更新が指定されています。更新後に再起動してください。")
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
			entry.Jitter, entry.PacketLoss = computeSLAMetrics(res.Target, res.Ok, res.Latency)
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
