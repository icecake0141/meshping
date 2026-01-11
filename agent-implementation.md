# agent.go のコード実装解説

このドキュメントは、agent.go 内の主要なコードブロックごとに、その動作と実装意図を詳細に解説したものです。

---

## 1. WebSocket 接続とハンドシェイク処理

```go
package main

import (
    "encoding/json"
    "log"
    "os"
    "time"

    "github.com/gorilla/websocket"
)

const (
    serverURL  = "ws://localhost:5000/agent"
    passphrase = "your_passphrase" // 管理サーバで発行されたパスフレーズ
    version    = "1.0.0"
)

type HandshakeMessage struct {
    Passphrase string `json:"passphrase"`
    Hostname   string `json:"hostname"`
    IPAddress  string `json:"ip_address"`
    Version    string `json:"version"`
}

func main() {
    ws, _, err := websocket.DefaultDialer.Dial(serverURL, nil)
    if err != nil {
        log.Fatal("WebSocket接続エラー:", err)
    }
    defer ws.Close()

    hostname, _ := os.Hostname()
    ipAddress := getLocalIP() // agent.go では getLocalIP() を利用（UDPで8.8.8.8:80へ接続しLocalAddr()からIP取得）
    handshake := HandshakeMessage{
        Passphrase: passphrase,
        Hostname:   hostname,
        IPAddress:  ipAddress,
        Version:    version,
    }
    if err := ws.WriteJSON(handshake); err != nil {
        log.Fatal("ハンドシェイク送信エラー:", err)
    }

    // ハンドシェイク応答を受信した後、以降の処理へ移行
    time.Sleep(2 * time.Second)
    // ... 続く処理 ...
}
```

**解説:**  
- `main` 関数では、管理サーバの WebSocket エンドポイントに接続し、エージェントの情報（ホスト名、IP アドレス、バージョン、パスフレーズ）を含むハンドシェイクメッセージを送信します。  
- 接続や送信に失敗した場合、エラーをログに出力してプログラムを終了します。

---

## 2. 監視対象リストの管理と ICMP 監視処理

```go
import (
    "net"
    "sync"
    "time"

    "golang.org/x/net/icmp"
    "golang.org/x/net/ipv4"
)

var (
    targets      []string
    targetsMutex sync.RWMutex
)

func updateTargets(newTargets []string) {
    targetsMutex.Lock()
    defer targetsMutex.Unlock()
    targets = newTargets
    log.Println("監視対象リスト更新:", targets)
}

func pingTarget(target string) (bool, float64) {
    c, err := icmp.ListenPacket("ip4:icmp", "0.0.0.0")
    if err != nil {
        log.Println("ICMPリスンエラー:", err)
        return false, 0
    }
    defer c.Close()

    msg := icmp.Message{
        Type: ipv4.ICMPTypeEcho,
        Code: 0,
        Body: &icmp.Echo{
            ID:   1,
            Seq:  1,
            Data: []byte("PING"),
        },
    }
    wb, _ := msg.Marshal(nil)
    dst, _ := net.ResolveIPAddr("ip4", target)
    start := time.Now()
    c.WriteTo(wb, dst)
    // タイムアウト処理等は追加検討可能
    duration := time.Since(start)
    return true, duration.Seconds() * 1000
}

func monitorLoop() {
    ticker := time.NewTicker(5 * time.Second)
    defer ticker.Stop()

    for range ticker.C {
        targetsMutex.RLock()
        currentTargets := make([]string, len(targets))
        copy(currentTargets, targets)
        targetsMutex.RUnlock()

        // 各ターゲットへ ICMP エコーリクエストを送信し、結果を測定
        for _, target := range currentTargets {
            success, latency := pingTarget(target)
            log.Printf("対象: %s, 結果: %v, 遅延: %.2fms\n", target, success, latency)
        }

        // ここで監視結果を管理サーバへ送信する処理を実装可能
    }
}

func startMonitoring() {
    go monitorLoop()
}
```

**解説:**  
- `updateTargets` 関数は、サーバから送信された新しい監視対象リストでグローバル変数 `targets` を更新します。  
- `pingTarget` 関数は、指定されたターゲットに対して ICMP エコーリクエストを送信し、応答時間（RTT）を測定します。  
- `monitorLoop` は 5 秒ごとに全ターゲットに対して監視処理を実行し、その結果をログに記録します。  
- `startMonitoring` によって、この監視ループがバックグラウンドで開始されます。

---

以上が agent.go の主な実装部分の解説です。
