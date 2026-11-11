//go:build windows

package procgroup

import (
	"os/exec"
	"strconv"
)

// Configure makes cancellation kill cmd's whole process tree. Windows has no
// process groups in the POSIX sense, so the tree is walked by taskkill /T,
// which reaches the grass84.bat -> python -> module chain. cmd must come from
// exec.CommandContext and not have been started yet.
func Configure(cmd *exec.Cmd) {
	cmd.Cancel = func() error {
		pid := strconv.Itoa(cmd.Process.Pid)
		if err := exec.Command("taskkill", "/T", "/F", "/PID", pid).Run(); err != nil {
			// The tree walk failed; still take down the direct child.
			return cmd.Process.Kill()
		}
		return nil
	}
	cmd.WaitDelay = waitDelay
}
