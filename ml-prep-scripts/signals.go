package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"sync"
	"syscall"
)

// exitInterrupted is the conventional status for a process ended by SIGINT.
const exitInterrupted = 130

// cancelOnShutdownSignal returns a context that is cancelled when the process
// receives SIGINT or SIGTERM. The plugin's Cancel button sends SIGTERM; before
// this, Go's default handler exited on the spot and every GRASS child was
// orphaned, still running. Cancelling the context instead lets each
// exec.CommandContext tear down its process tree (see package procgroup).
//
// A second signal exits immediately, for when cleanup itself is stuck.
func cancelOnShutdownSignal(parent context.Context) (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(parent)
	sigCh := make(chan os.Signal, 2)
	stopped := make(chan struct{})
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)

	go func() {
		select {
		case sig := <-sigCh:
			fmt.Fprintf(os.Stderr, "[CANCELLED] %s received, stopping child processes...\n", sig)
			cancel()
		case <-ctx.Done():
			return
		}
		// ctx is already done here, so only stop() ends the wait.
		select {
		case sig := <-sigCh:
			fmt.Fprintf(os.Stderr, "[CANCELLED] second %s, exiting without cleanup\n", sig)
			os.Exit(exitInterrupted)
		case <-stopped:
		}
	}()

	var once sync.Once
	stop := func() {
		once.Do(func() {
			signal.Stop(sigCh)
			close(stopped)
			cancel()
		})
	}
	return ctx, stop
}

// exitIfCancelled ends the run with exitInterrupted once ctx is cancelled, so
// a stage killed by cancellation is not reported as a pipeline failure. The
// stages return only after their children are reaped and their temp
// directories removed, so nothing is left behind.
func exitIfCancelled(ctx context.Context) {
	if ctx.Err() == nil {
		return
	}
	fmt.Fprintln(os.Stderr, "[CANCELLED] pipeline stopped; child processes cleaned up")
	os.Exit(exitInterrupted)
}
