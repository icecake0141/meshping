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
	"math"
	"testing"
)

func TestToJSON(t *testing.T) {
	payload := map[string]string{"status": "ok"}
	got := toJSON(payload)
	if got == "" {
		t.Fatal("expected non-empty JSON string")
	}
}

func TestPingAllTargetsUsesTargetsSnapshot(t *testing.T) {
	originalPingTarget := pingTarget
	t.Cleanup(func() { pingTarget = originalPingTarget })

	calls := make(chan string, 2)
	pingTarget = func(target string) (bool, float64) {
		calls <- target
		return true, 1.23
	}

	targetsMutex.Lock()
	targets = []string{"10.0.0.1", "10.0.0.2"}
	targetsMutex.Unlock()

	results := pingAllTargets()
	if len(results) != 2 {
		t.Fatalf("expected 2 results, got %d", len(results))
	}
	for _, res := range results {
		if !res.Ok {
			t.Fatalf("expected ok result for %s", res.Target)
		}
	}

	seen := map[string]bool{}
	close(calls)
	for target := range calls {
		seen[target] = true
	}
	if !seen["10.0.0.1"] || !seen["10.0.0.2"] {
		t.Fatalf("expected ping calls for all targets, got %+v", seen)
	}
}

// resetTargetHistory clears the rolling window state between sub-tests.
func resetTargetHistory() {
	targetHistoryMutex.Lock()
	targetHistory = map[string]*targetWindow{}
	targetHistoryMutex.Unlock()
}

func TestComputeSLAMetrics_AllSuccess(t *testing.T) {
	resetTargetHistory()

	// Feed 4 successful pings with known latencies.
	latencies := []float64{10.0, 12.0, 11.0, 13.0}
	var jitter, packetLoss float64
	for _, l := range latencies {
		jitter, packetLoss = computeSLAMetrics("192.0.2.1", true, l)
	}

	if packetLoss != 0 {
		t.Fatalf("expected 0%% packet loss, got %.2f", packetLoss)
	}

	// Expected jitter = mean(|12-10|, |11-12|, |13-11|) = mean(2, 1, 2) = 5/3 ≈ 1.6667
	expectedJitter := (2.0 + 1.0 + 2.0) / 3.0
	if math.Abs(jitter-expectedJitter) > 1e-9 {
		t.Fatalf("expected jitter %.6f, got %.6f", expectedJitter, jitter)
	}
}

func TestComputeSLAMetrics_PacketLoss(t *testing.T) {
	resetTargetHistory()

	// 3 failures, 1 success → 75% packet loss.
	computeSLAMetrics("192.0.2.2", false, 0)
	computeSLAMetrics("192.0.2.2", false, 0)
	computeSLAMetrics("192.0.2.2", false, 0)
	_, packetLoss := computeSLAMetrics("192.0.2.2", true, 20.0)

	if math.Abs(packetLoss-75.0) > 1e-9 {
		t.Fatalf("expected 75%% packet loss, got %.2f", packetLoss)
	}
}

func TestComputeSLAMetrics_SinglePingNoJitter(t *testing.T) {
	resetTargetHistory()

	jitter, packetLoss := computeSLAMetrics("192.0.2.3", true, 15.0)

	if jitter != 0 {
		t.Fatalf("expected jitter 0 with single ping, got %.6f", jitter)
	}
	if packetLoss != 0 {
		t.Fatalf("expected 0%% packet loss, got %.2f", packetLoss)
	}
}

func TestComputeSLAMetrics_RollingWindowEviction(t *testing.T) {
	resetTargetHistory()

	// Fill window beyond slaWindowSize with failures, then add successes.
	for i := 0; i < slaWindowSize; i++ {
		computeSLAMetrics("192.0.2.4", false, 0)
	}
	// Now add slaWindowSize successes — old failures should be evicted.
	for i := 0; i < slaWindowSize; i++ {
		computeSLAMetrics("192.0.2.4", true, float64(10+i))
	}
	_, packetLoss := computeSLAMetrics("192.0.2.4", true, 30.0)

	if packetLoss != 0 {
		t.Fatalf("expected 0%% packet loss after eviction, got %.2f", packetLoss)
	}
}

func TestIsVersionBehind(t *testing.T) {
	cases := []struct {
		current string
		target  string
		want    bool
	}{
		{current: "1.0.0", target: "1.0.1", want: true},
		{current: "1.2.0", target: "1.1.9", want: false},
		{current: "1.0", target: "1.0.0", want: false},
		{current: "", target: "2.0.0", want: true},
	}

	for _, c := range cases {
		if got := isVersionBehind(c.current, c.target); got != c.want {
			t.Fatalf("isVersionBehind(%q, %q) = %v, want %v", c.current, c.target, got, c.want)
		}
	}
}
