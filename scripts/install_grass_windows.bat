@echo off
REM ============================================================================
REM  MLPREP Detection V4 - GRASS GIS dependency installer (Windows)
REM
REM  Installs what the plugin's EIL slope-unit stage needs:
REM     1. GRASS GIS 8.x
REM     2. the r.slopeunits addon  (the engine calls r.slopeunits.create)
REM
REM  Usage:
REM     install_grass_windows.bat              install anything missing
REM     install_grass_windows.bat /check       report only, change nothing
REM     install_grass_windows.bat /addon-only  skip GRASS, install the addon
REM
REM  The GRASS installer is ~940 MB. If QGIS was installed through OSGeo4W you
REM  may prefer to add GRASS from the OSGeo4W installer instead, so the two
REM  share one GDAL - run osgeo4w-setup.exe and tick "grass".
REM ============================================================================

setlocal enabledelayedexpansion

set "GRASS_VERSION=8.4.2"
set "GRASS_SETUP=WinGRASS-%GRASS_VERSION%-1-Setup.exe"
set "GRASS_URL=https://grass.osgeo.org/grass84/binary/mswindows/native/%GRASS_SETUP%"
set "ADDON=r.slopeunits"
set "PROBE_MODULE=r.slopeunits.create"
REM Newest first; matches _ALIASES["grass"] in platform_utils.py. QGIS 3.44
REM bundles grass85.
set "GRASS_NAMES=grass86.bat grass85.bat grass84.bat grass83.bat grass82.bat grass.bat"

REM The plugin passes the addon directory it checks (and that it points GRASS
REM at). Run by hand, fall back to GRASS's own Windows default.
if not defined GRASS_ADDON_BASE set "GRASS_ADDON_BASE=%APPDATA%\GRASS8\addons"

set "MODE=install"
if /i "%~1"=="/check"      set "MODE=check"
if /i "%~1"=="/addon-only" set "MODE=addon"
if /i "%~1"=="/?"          goto :usage
if /i "%~1"=="--help"      goto :usage

echo.
echo ============================================================
echo   MLPREP - GRASS GIS dependency setup
echo ============================================================
echo.

REM ---------------------------------------------------------------- find GRASS
call :find_grass
REM Mirrors platform_utils.find_grass(): the GRASS the plugin already resolved
REM (passed in MLPREP_GRASS_EXE), then versioned Program Files dirs, the GRASS
REM bundled inside QGIS, OSGeo4W, then PATH. Missing the QGIS-bundled copy
REM would trigger a needless ~940 MB GRASS download.
set "GRASS_EXE="
if defined MLPREP_GRASS_EXE if exist "%MLPREP_GRASS_EXE%" set "GRASS_EXE=%MLPREP_GRASS_EXE%"
if defined GRASS_EXE exit /b 0
for %%R in ("%ProgramFiles%" "%ProgramFiles(x86)%") do (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%%~R\GRASS GIS *" 2^>nul') do (
        for %%B in (%GRASS_NAMES%) do (
            if not defined GRASS_EXE if exist "%%~R\%%D\%%B" set "GRASS_EXE=%%~R\%%D\%%B"
        )
    )
)
for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles%\QGIS *" 2^>nul') do (
    for %%B in (%GRASS_NAMES%) do (
        if not defined GRASS_EXE if exist "%ProgramFiles%\%%D\bin\%%B" set "GRASS_EXE=%ProgramFiles%\%%D\bin\%%B"
    )
    for /d %%G in ("%ProgramFiles%\%%D\apps\grass\grass*") do (
        for %%B in (%GRASS_NAMES%) do (
            if not defined GRASS_EXE if exist "%%~G\bin\%%B" set "GRASS_EXE=%%~G\bin\%%B"
        )
    )
)
for %%R in ("C:\OSGeo4W\bin" "C:\OSGeo4W64\bin") do (
    for %%B in (%GRASS_NAMES%) do (
        if not defined GRASS_EXE if exist "%%~R\%%B" set "GRASS_EXE=%%~R\%%B"
    )
)
if not defined GRASS_EXE (
    for %%B in (%GRASS_NAMES%) do (
        if not defined GRASS_EXE (
            for /f "delims=" %%P in ('where %%B 2^>nul') do (
                if not defined GRASS_EXE set "GRASS_EXE=%%P"
            )
        )
    )
)
exit /b 0
if not defined GRASS_EXE goto :fail
if not "!ADDON_OK!"=="1" goto :fail
endlocal & exit /b 0

REM ============================================================ subroutines ===

:find_grass
REM Mirrors toolpath.Grass() in the plugin: versioned Program Files dirs first,
REM then OSGeo4W, then PATH.
set "GRASS_EXE="
for %%R in ("%ProgramFiles%" "%ProgramFiles(x86)%") do (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%%~R\GRASS GIS *" 2^>nul') do (
        if not defined GRASS_EXE (
            for %%B in (grass.bat grass84.bat grass83.bat grass82.bat) do (
                if not defined GRASS_EXE if exist "%%~R\%%D\%%B" set "GRASS_EXE=%%~R\%%D\%%B"
            )
        )
    )
)
for %%R in ("C:\OSGeo4W\bin" "C:\OSGeo4W64\bin") do (
    for %%B in (grass.bat grass84.bat grass83.bat grass82.bat) do (
        if not defined GRASS_EXE if exist "%%~R\%%B" set "GRASS_EXE=%%~R\%%B"
    )
)
if not defined GRASS_EXE (
    for %%B in (grass.bat grass84.bat grass83.bat) do (
        if not defined GRASS_EXE (
            for /f "delims=" %%P in ('where %%B 2^>nul') do (
                if not defined GRASS_EXE set "GRASS_EXE=%%P"
            )
        )
    )
)
exit /b 0

:wait_for_grass
REM %1 = seconds to wait for the installer to finish writing its directory.
set /a WAITED=0
:wait_loop
call :find_grass
if defined GRASS_EXE exit /b 0
if !WAITED! GEQ %1 exit /b 1
REM ping is the portable batch sleep.
ping -n 6 127.0.0.1 >nul
set /a WAITED+=5
goto :wait_loop

:check_addon
set "ADDON_OK="
if not defined GRASS_EXE exit /b 0
if exist "%GRASS_ADDON_BASE%\scripts\%PROBE_MODULE%.py" set "ADDON_OK=1"
if exist "%GRASS_ADDON_BASE%\scripts\%PROBE_MODULE%.bat" set "ADDON_OK=1"
if exist "%GRASS_ADDON_BASE%\bin\%PROBE_MODULE%.exe"     set "ADDON_OK=1"
exit /b 0

:manual_addon_install
REM r.slopeunits is pure Python (r.slopeunits.create/.clean/.metrics/.optimize),
REM so the modules can be placed directly in the GRASS addon directory without
REM compiling anything.
set "ADDON_SCRIPTS=%GRASS_ADDON_BASE%\scripts"
set "ADDON_BIN=%GRASS_ADDON_BASE%\bin"
if not exist "%ADDON_SCRIPTS%" mkdir "%ADDON_SCRIPTS%" 2>nul
if not exist "%ADDON_BIN%" mkdir "%ADDON_BIN%" 2>nul

REM Prefer the copy bundled with the plugin: g.extension fetches precompiled
REM addons from wingrass.fsv.cvut.cz, which fails outright on networks that
REM cannot resolve that host ("getaddrinfo failed"). The bundled copy needs no
REM network at all.
set "BUNDLED=%~dp0..\vendor\grass_addons\r.slopeunits"

if exist "%BUNDLED%\r.slopeunits.create.py" (
    echo    Installing from the copy bundled with the plugin ^(no network needed^)
    for %%M in (r.slopeunits.create r.slopeunits.clean r.slopeunits.metrics r.slopeunits.optimize) do (
        copy /y "%BUNDLED%\%%M.py" "%ADDON_SCRIPTS%\%%M.py" >nul
        if exist "%ADDON_SCRIPTS%\%%M.py" (
            REM GRASS launches script addons on Windows through a .bat shim.
            > "%ADDON_BIN%\%%M.bat" echo @"%%GRASS_PYTHON%%" "%%~dp0..\scripts\%%M.py" %%*
            echo      installed %%M
        )
    )
    exit /b 0
)

echo    No bundled copy found; downloading from GitHub instead.
set "RAW=https://raw.githubusercontent.com/OSGeo/grass-addons/grass8/src/raster/r.slopeunits"
for %%M in (r.slopeunits.create r.slopeunits.clean r.slopeunits.metrics r.slopeunits.optimize) do (
    echo    Fetching %%M...
    curl.exe -sL --fail -o "%ADDON_SCRIPTS%\%%M.py" "%RAW%/%%M/%%M.py"
    if exist "%ADDON_SCRIPTS%\%%M.py" (
        > "%ADDON_BIN%\%%M.bat" echo @"%%GRASS_PYTHON%%" "%%~dp0..\scripts\%%M.py" %%*
    ) else (
        echo    [WARN] could not download %%M
    )
)
exit /b 0

:usage
echo Usage: %~nx0 [/check ^| /addon-only]
echo   (no option)   install GRASS and the %ADDON% addon if missing
echo   /check        report what is present, change nothing
echo   /addon-only   install only the addon (GRASS must already exist^)
endlocal & exit /b 0

:fail
echo.
echo Setup did not complete. See the messages above.
endlocal & exit /b 1
