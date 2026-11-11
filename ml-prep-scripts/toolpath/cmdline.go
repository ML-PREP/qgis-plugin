package toolpath

import (
	"os"
	"strings"
)

// This file is deliberately NOT build-tagged. The cmd.exe command-line rules
// are pure string manipulation, and keeping them out of command_windows.go
// means they can be unit-tested on any platform rather than only on Windows —
// where, in practice, they would go untested until a user hit them.

// NeedsShell reports whether an executable must be run through cmd.exe.
// CreateProcess cannot start a batch file directly, and GRASS on Windows ships
// its launcher as grass84.bat.
func NeedsShell(exe string) bool {
	lower := strings.ToLower(exe)
	return strings.HasSuffix(lower, ".bat") || strings.HasSuffix(lower, ".cmd")
}

// QuoteArg wraps an argument for a cmd.exe command line when it contains
// whitespace. Arguments here are file paths and numbers; an embedded quote is
// dropped rather than escaped, because cmd.exe's escaping rules differ from the
// C runtime's and a half-escaped quote would silently truncate the command.
func QuoteArg(s string) string {
	if s == "" {
		return `""`
	}
	s = strings.ReplaceAll(s, `"`, "")
	if !strings.ContainsAny(s, " \t") {
		return s
	}
	return `"` + s + `"`
}

// BuildCmdLine assembles the raw command line for running `exe` with `args`
// through cmd.exe.
//
// Two details make this hand-built rather than left to exec.Command:
//
//   - argv[0] must be present. cmd.exe skips the first token when parsing its
//     own command line, so omitting it would silently swallow "/d".
//   - "/s" plus wrapping everything after "/c" in one quote pair is the
//     documented way to stop cmd.exe applying its quote-stripping heuristic to
//     a path like C:\Program Files\GRASS GIS 8.4\grass84.bat.
func BuildCmdLine(comspec, exe string, args []string) string {
	parts := make([]string, 0, len(args)+1)
	parts = append(parts, QuoteArg(exe))
	for _, a := range args {
		parts = append(parts, QuoteArg(a))
	}
	return QuoteArg(comspec) + ` /d /s /c "` + strings.Join(parts, " ") + `"`
}

// Comspec is the command interpreter to shell through.
func Comspec() string {
	if v := os.Getenv("COMSPEC"); v != "" {
		return v
	}
	return "cmd.exe"
}
