//go:build !windows

package toolpath

import (
	"context"
	"os/exec"
)

// Command builds an exec.Cmd bound to ctx. On POSIX no shell indirection is
// ever required.
func Command(ctx context.Context, exe string, args ...string) *exec.Cmd {
	return exec.CommandContext(ctx, exe, args...)
}
