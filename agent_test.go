package main

import (
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
