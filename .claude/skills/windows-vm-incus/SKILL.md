---
name: windows-vm-incus
description: Create and manage Windows VMs using Incus for local testing. Use when the user needs a Windows environment for testing, CI validation, or running Windows-specific tools.
allowed-tools: Read, Bash, Glob, Grep
---

# Windows VM on Incus

Create and manage Windows VMs using Incus for local testing. Use when the user needs a Windows environment for testing, CI validation, or running Windows-specific tools.

## Quick Start: Launch an Existing Image

```bash
# List available images
incus image list | grep win

# Launch a VM from the image
incus init win11e win-test --vm -c security.secureboot=false -c limits.cpu=4 -c limits.memory=8GB
incus config device add win-test iso-agent disk source=agent:config
incus start win-test

# Wait for agent (may take several minutes on first boot - see OOBE section below)
for i in $(seq 1 30); do
    incus exec win-test -- cmd /c "echo ready" 2>/dev/null && break
    sleep 10
done

# Stop and delete when done
incus stop win-test
incus delete win-test
```

## Executing Commands in the VM

**Use `cmd /c` not PowerShell for reliable command execution.** PowerShell through `incus exec` has quoting issues with special characters (`$`, `\`, pipes).

```bash
# GOOD: cmd /c with simple commands
incus exec win-test -- cmd /c "echo hello"
incus exec win-test -- cmd /c "dir C:\Users\Administrator"
incus exec win-test -- cmd /c "node --version"

# GOOD: PowerShell for simple commands (no special chars)
incus exec win-test -- powershell -Command "Get-ChildItem C:\Users\Administrator"

# BAD: PowerShell with $ (gets interpreted by both bash AND powershell)
incus exec win-test -- powershell -Command "$env:PATH"  # FAILS

# GOOD: For complex commands, write a .bat or .js file, push it, run it
cat > /tmp/script.bat << 'BAT'
set PATH=C:\mytools;%PATH%
node myscript.js
BAT
incus file push /tmp/script.bat win-test/C:/Users/Administrator/script.bat
incus exec win-test -- cmd /c "C:\Users\Administrator\script.bat"
```

**CRITICAL: `set` in batch files is space-sensitive.**
```bat
set BUNDLE_ROOT=C:\path\to\bundle&& next-command
@REM NOT: set BUNDLE_ROOT=C:\path\to\bundle && next-command
@REM The trailing space becomes part of the value!
```

## Pushing and Pulling Files

```bash
# Push files - use /C:/ prefix for Windows paths
incus file push local-file.zip win-test/C:/Users/Administrator/file.zip

# Push directories recursively
incus file push -r local-dir/ win-test/C:/Users/Administrator/dest/

# Pull files
incus file pull win-test/C:/Users/Administrator/results.txt ./

# For large transfers with many files, tar locally and extract in VM:
tar czf /tmp/payload.tar.gz my-directory/
incus file push /tmp/payload.tar.gz win-test/C:/Users/Administrator/payload.tar.gz
incus exec win-test -- cmd /c "cd C:\Users\Administrator && tar xzf payload.tar.gz"
```

**The VM has NO internet access by default.** All tools must be pushed from the host:
- Node.js: Download zip from nodejs.org, push, extract
- Python: Download installer, push, run silently
- Any npm packages: Install locally, tar with node_modules, push

### Installing Node.js (no internet)

```bash
# Download on host
curl -sL -o /tmp/node.zip "https://nodejs.org/dist/v22.14.0/node-v22.14.0-win-x64.zip"

# Push and extract in VM
incus file push /tmp/node.zip win-test/C:/Users/Administrator/node.zip
incus exec win-test -- powershell -Command 'Expand-Archive -Path C:\Users\Administrator\node.zip -DestinationPath C:\Users\Administrator -Force'

# Use with full path (not in global PATH)
incus exec win-test -- cmd /c "C:\Users\Administrator\node-v22.14.0-win-x64\node.exe --version"

# For npm/npx, use cmd (not PowerShell - .cmd files can't be piped in PS)
incus exec win-test -- cmd /c "set PATH=C:\Users\Administrator\node-v22.14.0-win-x64;%PATH%&& npm --version"
```

## Building a Windows Image from Scratch

Uses the `incus-windows` tool from https://github.com/antifob/incus-windows

### On NixOS

The tool needs `xorriso` (for ISO repacking) and `python3` (for the click.py monitor).

**Do NOT use `nix-shell --run`** -- it overrides PATH and Python's subprocess calls won't find `incus`. Instead, symlink xorriso into PATH:

```bash
git clone --depth=1 https://github.com/antifob/incus-windows.git /tmp/incus-windows
cd /tmp/incus-windows

# Symlink xorriso into PATH
XORRISO=$(nix eval --impure --raw --expr 'with import <nixpkgs> {}; "${xorriso}/bin/xorriso"' 2>/dev/null)
mkdir -p /tmp/win-vm/bin && ln -sf "$XORRISO" /tmp/win-vm/bin/xorriso
export PATH="/tmp/win-vm/bin:$PATH"

# MUST patch click.py first (env={'LANG':'C'} wipes PATH on NixOS)
sed -i "s/env={'LANG': 'C'}/env={**os.environ, 'LANG': 'C'}/" tools/click.py

# Build (downloads ISO + virtio drivers, ~6GB + 700MB)
# Takes 30-60 minutes total (download + unattended Windows install)
sh build.sh 11e

# Import the image
sh tools/import.sh ./output/win11e/
```

### Build Process Details

1. Downloads Win11 Enterprise eval ISO (~5GB) and virtio drivers (~700MB) - cached in `./isos/`
2. Repacks virtio ISO with `Autounattend.xml` for unattended install
3. Creates an incus VM, boots from ISO
4. `click.py` monitors the console, spams Enter to boot from DVD
5. Waits for Windows to install and VM to stop (auto-sysprep)
6. Exports the disk image as `disk.qcow2` + `incus.tar.xz`

### Available Targets

| Target | Description |
|--------|-------------|
| `11e` | Windows 11 Enterprise (24H2) |
| `10e` | Windows 10 Enterprise (22H2) |
| `2025` | Windows Server 2025 |
| `2022` | Windows Server 2022 |

## Headless SPICE Interaction (Screenshots + Keystrokes)

When the VM needs graphical interaction but you have no display, use xvfb + spicy + xdotool:

```bash
# Take a screenshot
nix-shell -p xvfb-run imagemagick xdotool --run '
  xvfb-run -s "-screen 0 1920x1080x24" bash -c "
    incus console --type=vga VM_NAME &
    sleep 6
    import -window root /tmp/screenshot.png
    kill %1 2>/dev/null
  "
'
# View the screenshot with: Read tool on /tmp/screenshot.png
```

### Sending Keystrokes

**You MUST click inside the spicy window first to grab VM input.**

```bash
nix-shell -p xvfb-run imagemagick xdotool --run '
  xvfb-run -s "-screen 0 1920x1080x24" bash -c "
    incus console --type=vga VM_NAME &
    sleep 6

    # REQUIRED: click to grab input
    xdotool mousemove 200 150
    xdotool click 1
    sleep 1

    # Now keystrokes reach the VM
    xdotool key super+r           # Win+R opens Run dialog
    sleep 2
    xdotool type --delay 50 \"powershell\"
    xdotool key ctrl+shift+Return # Run as admin
    sleep 3
    xdotool key Left              # Select Yes on UAC
    xdotool key Return
    sleep 3

    # Type commands in the PowerShell window
    xdotool type --delay 30 \"dir C:\\\\\"
    xdotool key Return
    sleep 2

    import -window root /tmp/after-commands.png
    kill %1 2>/dev/null
  "
'
```

**Tips:**
- Take screenshots between steps to verify what happened
- Keyboard shortcuts (Win+R, Tab, Enter, Alt+A) are more reliable than mouse clicks
- The spicy window scales the VM, so mouse coordinates are relative to xvfb, not VM resolution
- After clicking, wait 1s before sending keys
- Use `--delay 30` or higher for `xdotool type` to avoid dropped characters

### Common SPICE Keystroke Sequences

| Task | Keystrokes |
|------|-----------|
| Open Run dialog | `super+r` |
| Open admin PowerShell | `super+r`, type `powershell`, `ctrl+shift+Return`, `Left`, `Return` (UAC) |
| Dismiss firewall dialog | `Tab`, `Return` (or `alt+a` for Allow) |
| Close a window | `alt+F4` |

## Agent Not Running After First Boot (OOBE)

After importing a sysprep'd image, Windows goes through OOBE on first boot. The incus agent won't work until OOBE completes AND the agent is installed from the config drive.

**Diagnosis:** `incus exec win-test -- cmd /c "echo test"` returns "VM agent isn't currently running"

**Fix (headless):**
1. Take a screenshot to see the VM state
2. If showing desktop: the agent needs manual installation
3. Use the SPICE keystroke technique above to:
   - Open admin PowerShell
   - Run `E:\incus-agent.exe install` (E: is the agent config drive)
   - Allow the firewall dialog
4. Once the agent installs, `incus exec` starts working immediately

**The agent config drive (E:) contains:**
- `incus-agent.exe` - the agent binary
- `server.crt`, `agent.crt` - TLS certificates
- `install.cmd` - install helper

**CRITICAL:** Run `incus-agent.exe install` from E: so it finds the certificates. If run from another directory, it fails with "Failed to read client certificate".

## Key Details

- **Default credentials:** `administrator` / `vagrant` (from Autounattend.xml, NOT admin/changeme as README says)
- **Disk size:** 60GB for Win11, 30GB for Server editions
- **Memory:** 8GB recommended (4GB minimum)
- **TPM:** Automatically added for Win11
- **Secure boot:** Disabled (required for incus compatibility)
- **ISO source:** Microsoft Evaluation Center (90-day trial, no key needed)
- **Network:** VM gets IPv6 via incus bridge but typically NO internet access
- **File paths:** Use `/C:/path` format for `incus file push/pull`
