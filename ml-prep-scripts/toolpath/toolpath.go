// Package toolpath resolves external executables (GRASS, GDAL, Python) across
// macOS, Windows and Linux.
//
// Every external command in this module goes through it, so adding a platform
// means editing one table rather than each call site. The QGIS plugin that
// launches this binary exports EnvGrassExe / EnvGdalBin with the paths it has
// already resolved; those always win, so the user's explicit choice in the
// plugin's Dependencies panel is authoritative.
package toolpath

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
)

// Environment variables set by the QGIS plugin.
const (
	EnvGrassExe = "MLPREP_GRASS_EXE"
	EnvGdalBin  = "MLPREP_GDAL_BIN"
)

// ExeSuffix is the platform's executable extension.
func ExeSuffix() string {
	if runtime.GOOS == "windows" {
		return ".exe"
	}
	return ""
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// expand turns a list of directory patterns into existing directories.
// Versioned install roots ("GRASS GIS 8.4", "grass84") are matched by glob so
// they do not have to be enumerated. Matches are sorted descending so a newer
// version is preferred over an older one.
func expand(patterns []string) []string {
	var dirs []string
	for _, pattern := range patterns {
		matches, err := filepath.Glob(pattern)
		if err != nil || len(matches) == 0 {
			continue
		}
		sort.Sort(sort.Reverse(sort.StringSlice(matches)))
		for _, m := range matches {
			if info, err := os.Stat(m); err == nil && info.IsDir() {
				dirs = append(dirs, m)
			}
		}
	}
	return dirs
}

// UserHome returns the user's home directory.
//
// This replaces os.Getenv("HOME"), which is empty on Windows and silently
// produced paths rooted at the current drive.
func UserHome() string {
	if home, err := os.UserHomeDir(); err == nil {
		return home
	}
	return ""
}

// GrassAddons is where g.extension installs addons such as r.slopeunits.
func GrassAddons() string {
	if runtime.GOOS == "windows" {
		if appData := os.Getenv("APPDATA"); appData != "" {
			return filepath.Join(appData, "GRASS8", "addons")
		}
	}
	return filepath.Join(UserHome(), ".grass8", "addons")
}

func grassSearchDirs() []string {
	switch runtime.GOOS {
	case "darwin":
		return []string{
			"/Applications/GRASS-*.app/Contents/Resources/bin",
			"/opt/homebrew/bin",
			"/usr/local/bin",
		}
	case "windows":
		return []string{
			`C:\Program Files\GRASS GIS *`,
			`C:\Program Files (x86)\GRASS GIS *`,
			`C:\OSGeo4W\bin`,
			`C:\OSGeo4W64\bin`,
			// QGIS bundles its own GRASS. On a standalone QGIS install this
			// is the only GRASS on the machine.
			`C:\Program Files\QGIS *\bin`,
			`C:\Program Files\QGIS *\apps\grass\grass*\bin`,
		}
	default:
		return []string{"/usr/bin", "/usr/local/bin", "/usr/lib/grass*/bin"}
	}
}

// grassNames covers both the unversioned launcher and the version-suffixed one
// used by most Linux distributions and by the Windows installers.
// QGIS 3.44 bundles grass85; newest first so a bundled GRASS is preferred
// over an older standalone one.
var grassNames = []string{"grass86", "grass85", "grass84", "grass83", "grass82", "grass"}

func grassExtensions() []string {
	if runtime.GOOS == "windows" {
		// GRASS on Windows ships a batch launcher, not a .exe.
		return []string{".bat", ".cmd", ".exe"}
	}
	return []string{""}
}

// Grass resolves the GRASS GIS launcher. Falls back to the bare name so the
// caller still gets a usable error from exec if GRASS is genuinely absent.
func Grass() string {
	if v := os.Getenv(EnvGrassExe); v != "" {
		return v
	}
	for _, name := range grassNames {
		if p, err := exec.LookPath(name); err == nil {
			return p
		}
	}
	for _, dir := range expand(grassSearchDirs()) {
		for _, name := range grassNames {
			for _, ext := range grassExtensions() {
				if p := filepath.Join(dir, name+ext); isFile(p) {
					return p
				}
			}
		}
	}
	return "grass"
}

func gdalSearchDirs() []string {
	switch runtime.GOOS {
	case "darwin":
		return []string{
			"/opt/homebrew/bin",
			"/usr/local/bin",
			"/Applications/QGIS*.app/Contents/MacOS/bin",
		}
	case "windows":
		return []string{
			`C:\OSGeo4W\bin`,
			`C:\OSGeo4W64\bin`,
			`C:\Program Files\QGIS *\bin`,
		}
	default:
		return []string{"/usr/bin", "/usr/local/bin"}
	}
}

// Gdal resolves a GDAL/OGR command line tool such as gdalwarp or ogrinfo.
//
// These are not on PATH on Windows outside the OSGeo4W shell, so the plugin
// exports the directory of the GDAL that QGIS itself ships via EnvGdalBin.
func Gdal(name string) string {
	binary := name + ExeSuffix()
	if dir := os.Getenv(EnvGdalBin); dir != "" {
		if p := filepath.Join(dir, binary); isFile(p) {
			return p
		}
	}
	if p, err := exec.LookPath(name); err == nil {
		return p
	}
	for _, dir := range expand(gdalSearchDirs()) {
		if p := filepath.Join(dir, binary); isFile(p) {
			return p
		}
	}
	return name
}

// GrassPython picks the interpreter to run inside a "grass --exec" session.
//
// GRASS puts its own etc/python on PYTHONPATH for the child process, so any
// Python 3 can import grass.script; preferred is the plugin's virtualenv
// interpreter, which is guaranteed to exist.
func GrassPython(preferred string) string {
	if preferred != "" && isFile(preferred) {
		return preferred
	}
	names := []string{"python3", "python"}
	if runtime.GOOS == "windows" {
		names = []string{"python", "python3"}
	}
	for _, name := range names {
		if p, err := exec.LookPath(name); err == nil {
			return p
		}
	}
	return names[0]
}
