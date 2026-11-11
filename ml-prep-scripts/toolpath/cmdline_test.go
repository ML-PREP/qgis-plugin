package toolpath

import (
	"strings"
	"testing"
)

func TestNeedsShellOnlyForBatchLaunchers(t *testing.T) {
	cases := map[string]bool{
		`C:\Program Files\GRASS GIS 8.4\grass84.bat`: true,
		`C:\OSGeo4W\bin\grass84.CMD`:                 true,
		`C:\OSGeo4W\bin\gdalwarp.exe`:                false,
		`/usr/bin/grass`:                             false,
	}
	for exe, want := range cases {
		if got := NeedsShell(exe); got != want {
			t.Errorf("NeedsShell(%q) = %v, want %v", exe, got, want)
		}
	}
}

func TestQuoteArgWrapsOnlyWhenNeeded(t *testing.T) {
	cases := map[string]string{
		`C:\OSGeo4W\bin\grass84.bat`:                 `C:\OSGeo4W\bin\grass84.bat`,
		`C:\Program Files\GRASS GIS 8.4\grass84.bat`: `"C:\Program Files\GRASS GIS 8.4\grass84.bat"`,
		`-c`:   `-c`,
		`5000`: `5000`,
		``:     `""`,
	}
	for arg, want := range cases {
		if got := QuoteArg(arg); got != want {
			t.Errorf("QuoteArg(%q) = %q, want %q", arg, got, want)
		}
	}
}

func TestQuoteArgStripsEmbeddedQuotes(t *testing.T) {
	// A half-escaped quote would truncate the command line silently.
	if got := QuoteArg(`C:\a"b\c.tif`); strings.Contains(got, `"`) {
		t.Errorf("QuoteArg left an embedded quote: %q", got)
	}
}

func TestBuildCmdLineKeepsArgv0(t *testing.T) {
	// cmd.exe skips the first token; without it "/d" would be swallowed.
	got := BuildCmdLine(`C:\Windows\system32\cmd.exe`, `C:\g\grass84.bat`, nil)
	if !strings.HasPrefix(got, `C:\Windows\system32\cmd.exe /d /s /c "`) {
		t.Errorf("BuildCmdLine = %q, want it to start with the interpreter", got)
	}
}

func TestBuildCmdLineWrapsTheWholeCommandOnce(t *testing.T) {
	// "/s" + one enclosing quote pair is what stops cmd.exe stripping the
	// quotes around a Program Files path.
	got := BuildCmdLine(
		`C:\Windows\system32\cmd.exe`,
		`C:\Program Files\GRASS GIS 8.4\grass84.bat`,
		[]string{"-c", `C:\My Data\dem.tif`, "--exec", "python", "slu.py"},
	)
	want := `C:\Windows\system32\cmd.exe /d /s /c ` +
		`""C:\Program Files\GRASS GIS 8.4\grass84.bat" -c "C:\My Data\dem.tif" --exec python slu.py"`
	if got != want {
		t.Errorf("BuildCmdLine mismatch:\n got: %s\nwant: %s", got, want)
	}
	if !strings.HasSuffix(got, `"`) {
		t.Error("command line must end with the closing quote of the /c argument")
	}
}

func TestBuildCmdLineQuotesEachArgumentIndependently(t *testing.T) {
	got := BuildCmdLine("cmd.exe", "a.bat", []string{"plain", "with space"})
	if !strings.Contains(got, ` plain `) {
		t.Errorf("unspaced argument should not be quoted: %s", got)
	}
	if !strings.Contains(got, `"with space"`) {
		t.Errorf("spaced argument should be quoted: %s", got)
	}
}

func TestComspecFallsBackWhenUnset(t *testing.T) {
	t.Setenv("COMSPEC", "")
	if got := Comspec(); got != "cmd.exe" {
		t.Errorf("Comspec() = %q, want cmd.exe", got)
	}
}

func TestComspecPrefersTheEnvironment(t *testing.T) {
	t.Setenv("COMSPEC", `C:\Windows\system32\cmd.exe`)
	if got := Comspec(); got != `C:\Windows\system32\cmd.exe` {
		t.Errorf("Comspec() = %q, want the COMSPEC value", got)
	}
}
