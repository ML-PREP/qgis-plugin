package main

import (
	"flag"
	"io"
	"testing"
)

func newTestFlags(t *testing.T, args ...string) (*flag.FlagSet, *string, *string) {
	t.Helper()
	fs := flag.NewFlagSet("zonal", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	dem := fs.String("dem", "", "")
	out := fs.String("outputDir", "output", "")
	if err := fs.Parse(args); err != nil {
		t.Fatalf("parse %v: %v", args, err)
	}
	return fs, dem, out
}

func TestResolveDEMArgKeepsTheFlagWhenNoPositionalIsGiven(t *testing.T) {
	fs, dem, _ := newTestFlags(t, "-dem", "a.tif")

	got, err := resolveDEMArg(fs, *dem)

	if err != nil || got != "a.tif" {
		t.Fatalf("got %q, %v; want a.tif", got, err)
	}
}

func TestResolveDEMArgReturnsEmptyWhenNoDEMIsGiven(t *testing.T) {
	fs, dem, _ := newTestFlags(t)

	got, err := resolveDEMArg(fs, *dem)

	if err != nil || got != "" {
		t.Fatalf("got %q, %v; want empty", got, err)
	}
}

func TestResolveDEMArgAcceptsAPositionalPath(t *testing.T) {
	fs, dem, _ := newTestFlags(t, "dem.tif")

	got, err := resolveDEMArg(fs, *dem)

	if err != nil || got != "dem.tif" {
		t.Fatalf("got %q, %v; want dem.tif", got, err)
	}
}

func TestResolveDEMArgParsesFlagsThatFollowThePositionalPath(t *testing.T) {
	// Go's flag package stops at the first non-flag, so without a re-parse
	// "-outputDir" here would be silently ignored.
	fs, dem, out := newTestFlags(t, "dem.tif", "-outputDir", "run1")

	got, err := resolveDEMArg(fs, *dem)

	if err != nil || got != "dem.tif" {
		t.Fatalf("got %q, %v; want dem.tif", got, err)
	}
	if *out != "run1" {
		t.Fatalf("outputDir = %q; want run1", *out)
	}
}

func TestResolveDEMArgRejectsBothFlagAndPositional(t *testing.T) {
	fs, dem, _ := newTestFlags(t, "-dem", "a.tif", "b.tif")

	if _, err := resolveDEMArg(fs, *dem); err == nil {
		t.Fatal("want an error when the DEM is given twice")
	}
}

func TestResolveDEMArgRejectsExtraPositionals(t *testing.T) {
	fs, dem, _ := newTestFlags(t, "a.tif", "b.tif")

	if _, err := resolveDEMArg(fs, *dem); err == nil {
		t.Fatal("want an error for an unexpected second path")
	}
}
