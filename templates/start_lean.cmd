@echo off
setlocal EnableDelayedExpansion

:: Set the bundle root directory (where this script lives)
set BUNDLE_ROOT=%~dp0
set BUNDLE_ROOT=%BUNDLE_ROOT:~0,-1%

:: Force VSCodium portable mode.  Short-circuits platform-specific path
:: detection so extensions and settings load consistently across OSes.
set VSCODE_PORTABLE=%BUNDLE_ROOT%\vscodium\data

:: If the student has a pre-existing elan install, the lean4 VS Code
:: extension unconditionally prepends %USERPROFILE%\.elan\bin to PATH
:: and queries that elan about the project's toolchain.  When elan
:: doesn't have our exact version installed, a modal "Lean version ...
:: is not installed" dialog appears.
::
:: Fix: junction the bundled Lean into the student's elan toolchains
:: directory so elan reports our toolchain as installed.  We also
:: reset PATH to a minimal known-good value and clear ELAN_HOME so
:: that students without elan fall cleanly through to our bundled
:: `lean` on PATH.
set PATH=%BUNDLE_ROOT%\lean\bin;%BUNDLE_ROOT%\git\cmd;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem
set ELAN_HOME=

:: Register the bundled toolchain with the student's elan if present
:: (no-op otherwise).  The encoded name is substituted at bundle
:: assembly time so the launcher doesn't have to parse strings at
:: runtime (cmd.exe string handling is fragile).
:: `mklink /J` creates a directory junction without admin privileges.
set TOOLCHAIN_ENCODED=@@TOOLCHAIN_ENCODED@@
if exist "%USERPROFILE%\.elan\bin\elan.exe" if not exist "%USERPROFILE%\.elan\toolchains\%TOOLCHAIN_ENCODED%" if not "%TOOLCHAIN_ENCODED%"=="" (
    if not exist "%USERPROFILE%\.elan\toolchains" mkdir "%USERPROFILE%\.elan\toolchains" >nul 2>&1
    mklink /J "%USERPROFILE%\.elan\toolchains\%TOOLCHAIN_ENCODED%" "%BUNDLE_ROOT%\lean" >nul 2>&1
)
set TOOLCHAIN_ENCODED=

:: Build LEAN_PATH from all package build directories
set LEAN_PATH=%BUNDLE_ROOT%\lean\lib\lean;%BUNDLE_ROOT%\project\.lake\build\lib\lean
for /d %%P in ("%BUNDLE_ROOT%\project\.lake\packages\*") do (
    if exist "%%P\.lake\build\lib\lean" (
        set LEAN_PATH=!LEAN_PATH!;%%P\.lake\build\lib\lean
    )
)

:: Open the configured file only on the first launch of this extracted
:: bundle. Later launches let VSCodium restore the student's own open tabs.
set OPEN_FILE=@@OPEN_FILE@@
set "OPEN_MARKER=%VSCODE_PORTABLE%\user-data\User\.lean-bundle-default-opened"
if exist "!OPEN_MARKER!" set OPEN_FILE=

:: Launch VSCodium with the project folder (and optional first-launch file).
:: Use `start` so the Command Prompt window closes immediately instead of
:: staying open for the entire VSCodium session. The empty "" is required
:: as the window-title argument (otherwise `start` treats the quoted path
:: as the title). Environment variables set above are inherited by `start`.
if defined OPEN_FILE (
    if exist "%BUNDLE_ROOT%\project\!OPEN_FILE!" (
        start "" "%BUNDLE_ROOT%\vscodium\VSCodium.exe" "%BUNDLE_ROOT%\project" "%BUNDLE_ROOT%\project\!OPEN_FILE!" %*
        type nul > "!OPEN_MARKER!"
    ) else (
        start "" "%BUNDLE_ROOT%\vscodium\VSCodium.exe" "%BUNDLE_ROOT%\project" %*
    )
) else (
    start "" "%BUNDLE_ROOT%\vscodium\VSCodium.exe" "%BUNDLE_ROOT%\project" %*
)
