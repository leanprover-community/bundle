---
name: windows-vm-incus
description: Create and manage Windows VMs using Incus for local testing. Use when the user needs a Windows environment for testing, CI validation, or running Windows-specific tools.
allowed-tools: Read, Bash, Glob, Grep
---

# Windows VM on Incus

## Quick Start: Launch an Existing Image

```bash
incus image list | grep win

incus init win11e win-test --vm -c security.secureboot=false -c limits.cpu=4 -c limits.memory=8GB
incus config device add win-test iso-agent disk source=agent:config
incus start win-test

# Wait for agent (may need OOBE handling on first boot - see below)
for i in $(seq 1 30); do
    incus exec win-test -- cmd /c "echo ready" 2>/dev/null && break
    sleep 10
done

incus stop win-test
incus delete win-test
```

## Executing Commands

**Prefer `cmd /c` over PowerShell.** PowerShell through `incus exec` has quoting issues with `$`, `\`, and pipes.

```bash
incus exec win-test -- cmd /c "echo hello"
incus exec win-test -- cmd /c "dir C:\Users\Administrator"

# For complex commands, write a .bat file, push it, run it
incus file push /tmp/script.bat win-test/C:/Users/Administrator/script.bat
incus exec win-test -- cmd /c "C:\Users\Administrator\script.bat"
```

**`set` in batch files is space-sensitive:**
```bat
set VAR=value&& next-command
@REM NOT: set VAR=value && next-command (trailing space becomes part of value!)
```

## Pushing and Pulling Files

```bash
incus file push local-file.zip win-test/C:/Users/Administrator/file.zip
incus file pull win-test/C:/Users/Administrator/results.txt ./

# For many files, tar first:
tar czf /tmp/payload.tar.gz my-directory/
incus file push /tmp/payload.tar.gz win-test/C:/Users/Administrator/payload.tar.gz
incus exec win-test -- cmd /c "cd C:\Users\Administrator && tar xzf payload.tar.gz"
```

## Building a Windows Image from Scratch

Uses [incus-windows](https://github.com/antifob/incus-windows). Downloads Win11 Enterprise eval ISO (~5GB) and virtio drivers (~700MB), runs unattended install, exports disk image.

### On NixOS

**Do NOT use `nix-shell --run`** -- it overrides PATH and Python's subprocess calls won't find `incus`. Symlink xorriso into PATH instead:

```bash
git clone --depth=1 https://github.com/antifob/incus-windows.git /tmp/incus-windows
cd /tmp/incus-windows

XORRISO=$(nix eval --impure --raw --expr 'with import <nixpkgs> {}; "${xorriso}/bin/xorriso"')
mkdir -p /tmp/win-vm/bin && ln -sf "$XORRISO" /tmp/win-vm/bin/xorriso
export PATH="/tmp/win-vm/bin:$PATH"

# Patch click.py: env={'LANG':'C'} wipes PATH on NixOS
sed -i "s/env={'LANG': 'C'}/env={**os.environ, 'LANG': 'C'}/" tools/click.py

# Build (30-60 min total)
sh build.sh 11e

# Import
sh tools/import.sh ./output/win11e/
```

Available targets: `11e` (Win11), `10e` (Win10), `2025`, `2022` (Server editions).

## Headless SPICE Interaction

When the VM needs graphical interaction (e.g. OOBE) but you have no display:

```bash
nix-shell -p xvfb-run imagemagick xdotool --run '
  xvfb-run -s "-screen 0 1920x1080x24" bash -c "
    incus console --type=vga VM_NAME &
    sleep 6

    # REQUIRED: click inside spicy window to grab VM input
    xdotool mousemove 200 150; xdotool click 1; sleep 1

    # Now keystrokes reach the VM
    xdotool key super+r; sleep 2
    xdotool type --delay 50 \"powershell\"
    xdotool key ctrl+shift+Return; sleep 3   # Run as admin
    xdotool key Left; sleep 0.3; xdotool key Return; sleep 3  # UAC yes

    xdotool type --delay 30 \"your-command-here\"
    xdotool key Return; sleep 2

    import -window root /tmp/screenshot.png
    kill %1 2>/dev/null
  "
'
```

**Tips:**
- Use `--delay 30` or higher for `xdotool type` to avoid dropped characters
- Keyboard shortcuts (Win+R, Tab, Enter) are more reliable than mouse clicks
- Take screenshots between steps to verify state

## Agent Not Running After First Boot (OOBE)

After launching from a sysprep'd image, Windows goes through OOBE. The incus agent won't work until the agent is installed from the config drive.

**Fix:** Use the headless SPICE technique above to open an admin PowerShell and run:
```
E:\incus-agent.exe install
```
The agent starts working immediately after install. **Must run from E:** so it finds the TLS certificates.

## Key Details

- **Credentials:** `administrator`/`vagrant` (from Autounattend.xml). The OEM scripts also create `admin`/`changeme`.
- **Secure boot:** Must be disabled (`-c security.secureboot=false`)
- **ISO source:** Microsoft Evaluation Center (90-day trial, no key needed)
- **Network:** VMs get IPv4 + IPv6 via the incus bridge with NAT (internet access works)
- **File paths:** Use `/C:/path` format for `incus file push/pull`
