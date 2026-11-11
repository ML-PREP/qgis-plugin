//go:build windows

package toolpath

import (
	"context"
	"os/exec"
	"syscall"
)

// Command builds an exec.Cmd bound to ctx, routing .bat/.cmd launchers
// through cmd.exe. The command-line assembly lives in cmdline.go so it can be
// tested off-Windows.
func Command(ctx context.Context, exe string, args ...string) *exec.Cmd {
	if !NeedsShell(exe) {
		return exec.CommandContext(ctx, exe, args...)
	}

	comspec := Comspec()
	cmd := exec.CommandContext(ctx, comspec)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CmdLine: BuildCmdLine(comspec, exe, args),
	}
	return cmd
}
