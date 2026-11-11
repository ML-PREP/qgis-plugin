package toolpath

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func writeStub(t *testing.T, dir, name string) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("stub"), 0o755); err != nil {
		t.Fatalf("cannot write stub %s: %v", path, err)
	}
	return path
}

func TestGdalPrefersDirectoryFromPluginEnvironment(t *testing.T) {
	// Arrange
	dir := t.TempDir()
	want := writeStub(t, dir, "gdalwarp"+ExeSuffix())
	t.Setenv(EnvGdalBin, dir)

	// Act
	got := Gdal("gdalwarp")

	// Assert
	if got != want {
		t.Errorf("Gdal(gdalwarp) = %q, want %q", got, want)
	}
}

func TestGdalFallsBackToBareNameWhenToolIsAbsent(t *testing.T) {
	// Arrange
	t.Setenv(EnvGdalBin, t.TempDir())
	const name = "mlprep-nonexistent-gdal-tool"

	// Act
	got := Gdal(name)

	// Assert — the bare name lets exec report a normal "not found" error.
	if got != name {
		t.Errorf("Gdal(%q) = %q, want the bare name", name, got)
	}
}

func TestGrassPrefersExecutableFromPluginEnvironment(t *testing.T) {
	// Arrange
	const want = "/custom/location/grass84"
	t.Setenv(EnvGrassExe, want)

	// Act
	got := Grass()

	// Assert
	if got != want {
		t.Errorf("Grass() = %q, want %q", got, want)
	}
}

func TestGrassAddonsIsAbsoluteAndPlatformSpecific(t *testing.T) {
	// Act
	got := GrassAddons()

	// Assert
	if !filepath.IsAbs(got) {
		t.Fatalf("GrassAddons() = %q, want an absolute path", got)
	}
	if runtime.GOOS == "windows" {
		if !strings.Contains(got, "GRASS8") {
			t.Errorf("GrassAddons() = %q, want the APPDATA GRASS8 location", got)
		}
		return
	}
	if !strings.Contains(got, ".grass8") {
		t.Errorf("GrassAddons() = %q, want the dotfile location", got)
	}
}

func TestUserHomeResolvesWithoutTheHomeVariable(t *testing.T) {
	// Arrange — HOME is unset on Windows, which is what broke the old code.
	// Act
	got := UserHome()

	// Assert
	if got == "" {
		t.Error("UserHome() = \"\", want the user's home directory")
	}
}

func TestExpandKeepsDirectoriesAndSkipsFilesAndMisses(t *testing.T) {
	// Arrange
	root := t.TempDir()
	realDir := filepath.Join(root, "grass84")
	if err := os.Mkdir(realDir, 0o755); err != nil {
		t.Fatal(err)
	}
	writeStub(t, root, "notadir")

	// Act
	got := expand([]string{
		filepath.Join(root, "grass*"),
		filepath.Join(root, "notadir"),
		filepath.Join(root, "missing*"),
	})

	// Assert
	if len(got) != 1 || got[0] != realDir {
		t.Errorf("expand() = %v, want [%s]", got, realDir)
	}
}

func TestExpandPrefersTheHighestVersionedMatch(t *testing.T) {
	// Arrange
	root := t.TempDir()
	for _, name := range []string{"GRASS GIS 8.3", "GRASS GIS 8.4"} {
		if err := os.Mkdir(filepath.Join(root, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	// Act
	got := expand([]string{filepath.Join(root, "GRASS GIS *")})

	// Assert
	if len(got) != 2 || filepath.Base(got[0]) != "GRASS GIS 8.4" {
		t.Errorf("expand() = %v, want the 8.4 directory first", got)
	}
}

func TestGrassPythonUsesThePreferredInterpreterWhenItExists(t *testing.T) {
	// Arrange
	want := writeStub(t, t.TempDir(), "python"+ExeSuffix())

	// Act
	got := GrassPython(want)

	// Assert
	if got != want {
		t.Errorf("GrassPython() = %q, want %q", got, want)
	}
}

func TestGrassPythonIgnoresAPreferredPathThatDoesNotExist(t *testing.T) {
	// Act
	got := GrassPython(filepath.Join(t.TempDir(), "gone", "python"))

	// Assert
	if strings.Contains(got, "gone") {
		t.Errorf("GrassPython() = %q, want a fallback interpreter", got)
	}
}
