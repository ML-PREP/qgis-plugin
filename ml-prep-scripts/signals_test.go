//go:build !windows

package main

import (
	"context"
	"os"
	"syscall"
	"testing"
	"time"
)

func TestShutdownSignalCancelsContext(t *testing.T) {
	// Arrange
	ctx, stop := cancelOnShutdownSignal(context.Background())
	defer stop()

	// Act: what the plugin's Cancel button sends via process.terminate().
	if err := syscall.Kill(os.Getpid(), syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}

	// Assert
	select {
	case <-ctx.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("SIGTERM did not cancel the pipeline context")
	}
}

func TestStopReleasesContextWithoutSignal(t *testing.T) {
	// Arrange
	ctx, stop := cancelOnShutdownSignal(context.Background())

	// Act
	stop()

	// Assert
	select {
	case <-ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("stop did not cancel the context")
	}
}
