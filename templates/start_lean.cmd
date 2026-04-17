@echo off
setlocal EnableDelayedExpansion

:: Set the bundle root directory (where this script lives)
set BUNDLE_ROOT=%~dp0
set BUNDLE_ROOT=%BUNDLE_ROOT:~0,-1%

:: Force VSCodium portable mode.  Short-circuits platform-specific path
:: detection so extensions and settings load consistently across OSes.
set VSCODE_PORTABLE=%BUNDLE_ROOT%\vscodium\data

:: Add lean and git to PATH
set PATH=%BUNDLE_ROOT%\lean\bin;%BUNDLE_ROOT%\git\cmd;%PATH%

:: Prevent elan from interfering
set ELAN_HOME=%BUNDLE_ROOT%\lean

:: Build LEAN_PATH from all package build directories
set LEAN_PATH=%BUNDLE_ROOT%\lean\lib\lean;%BUNDLE_ROOT%\project\.lake\build\lib\lean
for /d %%P in ("%BUNDLE_ROOT%\project\.lake\packages\*") do (
    if exist "%%P\.lake\build\lib\lean" (
        set LEAN_PATH=!LEAN_PATH!;%%P\.lake\build\lib\lean
    )
)

:: Determine which file to open (set during bundle assembly).
set OPEN_FILE=@@OPEN_FILE@@

:: Launch VSCodium with the project folder (and optional file).
:: Use `start` so the Command Prompt window closes immediately instead of
:: staying open for the entire VSCodium session. The empty "" is required
:: as the window-title argument (otherwise `start` treats the quoted path
:: as the title). Environment variables set above are inherited by `start`.
if defined OPEN_FILE (
    if exist "%BUNDLE_ROOT%\project\!OPEN_FILE!" (
        start "" "%BUNDLE_ROOT%\vscodium\VSCodium.exe" "%BUNDLE_ROOT%\project" "%BUNDLE_ROOT%\project\!OPEN_FILE!" %*
    ) else (
        start "" "%BUNDLE_ROOT%\vscodium\VSCodium.exe" "%BUNDLE_ROOT%\project" %*
    )
) else (
    start "" "%BUNDLE_ROOT%\vscodium\VSCodium.exe" "%BUNDLE_ROOT%\project" %*
)
