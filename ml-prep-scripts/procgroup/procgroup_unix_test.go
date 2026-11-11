//go:build !windows

package procgroup

import (
	"bufio"
	"context"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// alive reports whether pid still exists. Signal 0 checks for existence
// without delivering anything.
func alive(pid int) bool {
	return syscall.Kill(pid, 0) == nil
}

// startTree launches a shell that backgrounds a grandchild and prints its PID,
// the same shape as grass -> python -> r.slopeunits.create.
func startTree(t *testing.T, ctx context.Context, script string) (*exec.Cmd, int) {
	t.Helper()
	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", script)
	Configure(cmd)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil {
		t.Fatalf("reading grandchild pid: %v", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(line))
	if err != nil {
		t.Fatalf("bad grandchild pid %q: %v", line, err)
	}
	return cmd, pid
}

func waitGone(t *testing.T, pid int, within time.Duration) {
	t.Helper()
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if !alive(pid) {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	syscall.Kill(pid, syscall.SIGKILL)
	t.Fatalf("grandchild %d survived cancellation", pid)
}

func TestCancelKillsGrandchildren(t *testing.T) {
	// Arrange
	ctx, cancel := context.WithCancel(context.Background())
	cmd, grandchild := startTree(t, ctx, "sleep 60 & echo $!; wait")

	// Act
	cancel()
	err := cmd.Wait()

	// Assert
	if err == nil {
		t.Fatal("expected Wait to report the cancellation")
	}
	waitGone(t, grandchild, 2*time.Second)
}

func TestCancelEscalatesWhenTermIsIgnored(t *testing.T) {
	// Arrange: the grandchild ignores SIGTERM, as a busy GRASS module may.
	ctx, cancel := context.WithCancel(context.Background())
	cmd, grandchild := startTree(t, ctx, "trap '' TERM; sleep 60 & echo $!; wait")

	// Act
	cancel()
	cmd.Wait()

	// Assert
	waitGone(t, grandchild, killGrace+2*time.Second)
}

func TestUncancelledCommandRunsNormally(t *testing.T) {
	// Arrange
	cmd := exec.CommandContext(context.Background(), "/bin/sh", "-c", "echo ok")
	Configure(cmd)

	// Act
	out, err := cmd.Output()

	// Assert
	if err != nil || strings.TrimSpace(string(out)) != "ok" {
		t.Fatalf("got %q, %v", out, err)
	}
}
