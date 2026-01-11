# Implementation Status Report (PoC Gap Summary)

## 1. Core PoC behaviors implemented (server.py / agent.go)

### Agent handshake & registration
- server.py: On the `/agent` namespace, the `handshake` event receives passphrase/hostname/ip_address/version, creates a pending record for new agents, and places approved agents into hold if the IP changes. It replies with the registration state.
- agent.go: Sends the handshake on startup and exits if the returned status is pending/hold.

### Approval flow
- server.py: Approving sets status=approved and assigns agent_id, then pushes the current targets to agents.
- server.py: Rejection sets status=blacklisted.

### Target list distribution (push)
- server.py: Admin API/form updates current_targets and pushes update_targets to all agents.
- agent.go: Receives update_targets, updates targets, and unblocks initial wait on first update.

### ICMP monitoring & reporting
- agent.go: Every 5 seconds, sends ICMP echo to all targets concurrently and records RTT in ms on success.
- agent.go: Sends results as MonitoringDataMessage over WebSocket.

### Data storage
- server.py: Persists monitoring_data to SQLite.

### 1-hour cache
- server.py: Keeps the last hour in recent_cache and serves it via /monitoring/<agent>/<target>.

## 2. Gaps vs specs.txt (unimplemented / partial)

### Unimplemented
- User management (registration/roles/login/password reset)
- Real-time monitoring UI (grid view with color status)
- Passphrase generation & encrypted exchange (including registration screen)
- 24-hour data retention (DB retention/cleanup policy)
- Agent-side 30-minute buffer with TTL

### Partial
- Hover visualization: 1-hour data API exists, UI is missing/unverified
- TLS: server.py sets an SSL context, but agent.go uses ws://

## 3. Baseline expectations vs. reality (for future issues)

- Expectation: TLS WebSocket communication → Reality: server SSL exists, agent uses ws://
- Expectation: real-time monitoring UI/hover UI → Reality: API exists in part, UI not implemented
- Expectation: 24-hour data retention → Reality: only 1-hour cache is implemented; DB retention is missing

## 4. Living checklist (gap tracking)

### Server
- [x] Handshake / provisional registration / hold handling
- [x] Admin approve / reject
- [x] Target list push
- [x] Monitoring data storage + 1-hour cache
- [ ] User management
- [ ] Real-time monitoring UI
- [ ] Hover UI
- [ ] Passphrase encryption / registration screen
- [ ] 24-hour retention
- [ ] TLS (agent wss support)

### Agent
- [x] ICMP monitoring (5s interval, concurrent)
- [x] Target update reception
- [x] Send-failure buffer
- [ ] 30-minute buffer (TTL)
