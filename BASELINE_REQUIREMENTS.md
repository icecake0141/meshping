# Meshping Baseline Requirements

**Document Purpose:** This document establishes a clear baseline of what is currently implemented in the Meshping PoC versus what is specified but not yet implemented. This helps align future work with actual behavior and avoids duplicate or mis-scoped enhancements.

**Last Updated:** 2026-01-11

---

## Overview

Meshping is a network monitoring system consisting of a management server (`server.py`) and distributed monitoring agents (`agent.go`). The full specification is documented in `specs.txt`, while this document tracks implementation status.

---

## ✅ Implemented Features (PoC Foundations)

### 1. Agent Registration & Authentication
**Status:** ✅ Implemented  
**Source Files:** `server.py` (lines 174-220), `agent.go` (lines 187-218)

- **Passphrase-based authentication**: Agents use a server-issued passphrase for initial handshake
- **Provisional registration**: New agents are placed in "pending" status awaiting admin approval
- **Agent status management**: Four states supported (pending, approved, hold, blacklisted)
- **IP address change detection**: Automatically detects IP changes and sets status to "hold" for re-approval
- **Agent ID assignment**: System assigns unique agent IDs upon approval

### 2. WebSocket Communication
**Status:** ✅ Implemented  
**Source Files:** `server.py` (lines 22, 167-262), `agent.go` (lines 189-244)

- **Bi-directional communication**: Server and agents communicate via WebSocket
- **Namespace separation**: Uses `/agent` namespace for agent connections
- **Connection handling**: Proper connect/disconnect event handling
- **Message types**: Handshake, registration_status, monitoring_data, server_message (update_targets)

### 3. Monitoring Target Management
**Status:** ✅ Implemented  
**Source Files:** `server.py` (lines 117-163), `agent.go` (lines 220-244)

- **Centralized target list**: Server maintains single source of truth for monitoring targets
- **Push distribution**: Target list updates are pushed to all connected agents in real-time
- **Admin UI for target management**: Web interface at `/admin/targets` for editing targets
- **API endpoint**: REST API at `/admin/update_targets` for programmatic updates
- **Dynamic target updates**: Agents receive and apply target list changes without restart

### 4. ICMP Monitoring
**Status:** ✅ Implemented  
**Source Files:** `agent.go` (lines 98-185, 254-298)

- **5-second interval**: Agents ping all targets every 5 seconds
- **Concurrent monitoring**: Parallel execution for multiple targets
- **RTT measurement**: Records round-trip time for successful pings
- **Result states**: Binary ok/fail status with latency information
- **Timeout handling**: 3-second timeout for ICMP requests (line 22)

### 5. Data Storage & Caching
**Status:** ✅ Implemented  
**Source Files:** `server.py` (lines 28-58, 60-61, 222-262, 266-286)

- **SQLite database**: Persistent storage for agents and monitoring data
- **Two DB models**: 
  - `Agent`: Stores agent metadata and status
  - `MonitoringData`: Stores time-series ping results
- **1-hour memory cache**: Recent monitoring data cached in memory (`recent_cache` dict)
- **Cache management**: Automatic cleanup of data older than 1 hour
- **Hybrid retrieval**: Serves data from cache when available, falls back to database

### 6. Data Transmission & Buffering
**Status:** ✅ Implemented  
**Source Files:** `agent.go` (lines 252-298)

- **Periodic reporting**: Agents send monitoring results every 5 seconds
- **Local buffer**: Agents buffer unsent data during connection issues (30-minute retention)
- **Retry mechanism**: Attempts to send buffered data on reconnection
- **Data format**: JSON with agent_id, target, timestamp, result, latency

### 7. Admin Dashboard
**Status:** ✅ Implemented  
**Source Files:** `server.py` (lines 72-83, 86-113, 141-163)

- **Agent list display**: Shows agents by status (pending, approved, hold)
- **Approval controls**: Buttons to approve or reject pending agents
- **Current targets display**: Shows currently configured monitoring targets
- **Target management UI**: Dedicated page for editing monitoring targets
- **Status updates**: Real-time reflection of agent status changes

### 8. TLS Support (Optional)
**Status:** ✅ Implemented (self-signed)  
**Source Files:** `server.py` (lines 289-294), `README.md` (lines 109-138)

- **HTTPS/WSS support**: Server can run with TLS enabled
- **Self-signed certificates**: Documentation for generating cert.pem and key.pem
- **Configurable**: Can run with or without TLS

---

## ❌ Specified But Not Implemented (Gaps)

### 1. User Management
**Status:** ❌ Not Implemented  
**Specification:** `specs.txt` (lines 51-56)

**Missing Features:**
- User registration system
- Login/logout functionality
- Role-based access control
- Password management/reset
- Session management
- Authentication for admin dashboard (currently open access)

**Impact:** Admin dashboard is accessible without authentication

### 2. Real-time Monitoring Visualization
**Status:** ❌ Not Implemented  
**Specification:** `specs.txt` (lines 45-49)

**Missing Features:**
- Matrix view (agents as rows, targets as columns)
- Color-coded cells (green=OK, red=fail, yellow=high latency)
- Real-time updates (per-second refresh)
- Cell hover tooltips with graphs
- Visual representation of monitoring status

**Current State:** Only admin dashboard for agent management exists; no monitoring data visualization

### 3. Historical Data Graphs
**Status:** ❌ Not Implemented  
**Specification:** `specs.txt` (lines 48-49)

**Missing Features:**
- Line graph display on cell hover
- 1-hour historical view per agent-target pair
- RTT visualization over time
- Interactive tooltips

**Current State:** API endpoint exists (`/monitoring/<agent_id>/<target>`) to retrieve data, but no frontend visualization

### 4. Data Retention Policy
**Status:** ⚠️ Partially Implemented  
**Specification:** `specs.txt` (lines 30-34)

**Missing Features:**
- Automatic cleanup of data older than 24 hours
- Configurable retention periods
- Database maintenance tasks

**Current State:** 
- 1-hour cache implemented and working
- Data is written to SQLite but never deleted
- No retention policy enforcement (database will grow indefinitely)

### 5. Agent Local Buffer Details
**Status:** ⚠️ Partially Implemented  
**Specification:** `specs.txt` (lines 67-68)

**Missing Features:**
- Explicit 30-minute buffer limit enforcement
- Buffer size limits
- Overflow handling

**Current State:**
- Basic buffering exists (`unsentBuffer` in agent.go)
- No explicit time-based or size-based limits
- Could potentially grow without bounds

### 6. Enhanced Security Features
**Status:** ❌ Not Implemented  
**Specification:** `specs.txt` (lines 13-21)

**Missing Features:**
- Encryption of agent information using passphrase
- Secure passphrase exchange mechanism
- TLS enforcement (currently optional)
- Validation of encrypted agent data

**Current State:**
- Passphrase is sent in plain JSON
- No encryption of sensitive data
- TLS is optional, not enforced

### 7. Error Handling & Monitoring
**Status:** ⚠️ Basic Implementation  
**Specification:** `specs.txt` (lines 73-75)

**Missing Features:**
- Visual indication of agent connection issues
- Alerting for data gaps
- Comprehensive error reporting
- Health check mechanisms

**Current State:**
- Basic error logging exists
- No user-facing error displays
- No alerting system

---

## 📊 Implementation Summary by Category

| Category | Implemented | Partially | Not Implemented | Total |
|----------|-------------|-----------|-----------------|-------|
| **Agent Management** | 2 | 0 | 1 (user mgmt) | 3 |
| **Communication** | 2 | 0 | 0 | 2 |
| **Monitoring** | 2 | 1 (buffering) | 0 | 3 |
| **Data Storage** | 2 | 1 (retention) | 0 | 3 |
| **Visualization** | 1 | 0 | 2 (real-time, graphs) | 3 |
| **Security** | 1 | 0 | 2 (encryption, user auth) | 3 |

**Overall Completion:** ~65% of specified features are fully implemented

---

## 🎯 Recommended Next Steps

Based on gap analysis, recommended implementation priorities:

1. **High Priority - Security**
   - Implement user authentication for admin dashboard
   - Enforce TLS for production deployments
   - Add encryption for passphrase exchange

2. **High Priority - Data Management**
   - Implement 24-hour data retention cleanup
   - Add buffer size limits to prevent memory issues

3. **Medium Priority - Visualization**
   - Build real-time monitoring matrix view
   - Add hover graphs using existing API endpoint

4. **Medium Priority - Operations**
   - Add health monitoring and alerting
   - Improve error visibility in UI

5. **Lower Priority - User Management**
   - Full user management system with roles/permissions

---

## 📝 Notes

- This document should be updated whenever features are added or removed
- Testing coverage: Basic tests exist in `tests/` directory but coverage is incomplete
- Production readiness: Current implementation is PoC quality; security review required before production use

---

## References

- Full specification: `specs.txt`
- Server implementation: `server.py`
- Agent implementation: `agent.go`
- Server documentation: `server-implementation.md`
- Agent documentation: `agent-implementation.md`
- Overall implementation notes: `implementation.txt`
