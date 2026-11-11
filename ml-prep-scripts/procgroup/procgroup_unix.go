//go:build !windows

package procgroup

import (
	"errors"
	"os/exec"
	"syscall"
	"time"
)

// Configure puts cmd in a process group of its own and replaces the default
// cancel behaviour (SIGKILL to the child alone) with SIGTERM to the whole
// group, escalating to SIGKILL after killGrace. cmd must come from
// exec.CommandContext and not have been started yet.
func Configure(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.Setpgid = true
	cmd.Cancel = func() error { return killGroup(cmd.Process.Pid) }
	cmd.WaitDelay = waitDelay
}

// killGroup signals every process in the group led by pgid. The delayed
// SIGKILL cannot hit an unrelated process: a PID is not reused while any
// member of the group it names is still alive, and once none is, the kill
// fails with ESRCH.
func killGroup(pgid int) error {
	err := syscall.Kill(-pgid, syscall.SIGTERM)
	time.AfterFunc(killGrace, func() {
		syscall.Kill(-pgid, syscall.SIGKILL)
	})
	if errors.Is(err, syscall.ESRCH) {
		return nil
	}
	return err
}
