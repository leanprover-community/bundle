#!/usr/bin/env python3
"""Test the Lean language server via LSP protocol.

Launches lean --server, sends LSP initialize + textDocument/didOpen,
and verifies the server responds and reports diagnostics.

Usage:
    python tests/test_lsp.py <lean_bin_dir> <lean_path> <project_file>
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def encode_lsp(msg: dict) -> bytes:
    """Encode a JSON-RPC message with LSP Content-Length header."""
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_lsp_message(stream) -> dict | None:
    """Read one LSP message from a byte stream. Returns None on EOF."""
    # Read headers
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.decode("ascii").strip()
        if not line:
            break  # empty line = end of headers
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()

    content_length = int(headers.get("Content-Length", 0))
    if content_length == 0:
        return None

    body = stream.read(content_length)
    if not body:
        return None

    return json.loads(body.decode("utf-8"))


def file_uri(path: Path) -> str:
    """Convert a file path to a file:// URI."""
    # On Windows, file URIs use file:///C:/path
    p = path.resolve().as_posix()
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + p


def test_lsp(lean_bin: Path, lean_path: str, project_file: Path, timeout: int = 60) -> list[str]:
    """Test the LSP server. Returns list of errors (empty = OK)."""
    errors: list[str] = []

    lean_exe = lean_bin / ("lean.exe" if sys.platform == "win32" else "lean")
    if not lean_exe.is_file():
        return [f"lean binary not found: {lean_exe}"]

    env = {**os.environ}
    env["LEAN_PATH"] = lean_path
    env["ELAN_HOME"] = str(lean_bin.parent)
    env["PATH"] = str(lean_bin) + os.pathsep + env.get("PATH", "")

    proc = subprocess.Popen(
        [str(lean_exe), "--server", "--threads=1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Collect stderr in background
    stderr_lines: list[str] = []
    def read_stderr():
        for line in proc.stderr:
            stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    try:
        # Send initialize
        init_msg = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "capabilities": {},
                "rootUri": file_uri(project_file.parent),
            },
        }
        proc.stdin.write(encode_lsp(init_msg))
        proc.stdin.flush()

        # Read initialize response
        resp = read_lsp_message(proc.stdout)
        if resp is None:
            return ["No response from lean --server (initialize)"]
        if "error" in resp:
            return [f"Initialize error: {resp['error']}"]
        if resp.get("id") != 0:
            return [f"Unexpected response id: {resp.get('id')}"]

        print(f"  Server initialized: {resp.get('result', {}).get('serverInfo', {})}")

        # Send initialized notification
        proc.stdin.write(encode_lsp({
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {},
        }))
        proc.stdin.flush()

        # Send textDocument/didOpen
        file_content = project_file.read_text(encoding="utf-8")
        proc.stdin.write(encode_lsp({
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": file_uri(project_file),
                    "languageId": "lean4",
                    "version": 1,
                    "text": file_content,
                },
            },
        }))
        proc.stdin.flush()

        # Wait for diagnostics (or timeout)
        got_diagnostics = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = read_lsp_message(proc.stdout)
            if msg is None:
                break
            method = msg.get("method", "")
            if method == "textDocument/publishDiagnostics":
                got_diagnostics = True
                diags = msg.get("params", {}).get("diagnostics", [])
                error_diags = [d for d in diags if d.get("severity") == 1]
                if error_diags:
                    for d in error_diags[:5]:
                        line = d.get("range", {}).get("start", {}).get("line", "?")
                        errors.append(f"Diagnostic error at line {line}: {d.get('message', '')[:200]}")
                else:
                    print(f"  Diagnostics received: {len(diags)} total, 0 errors")
                break
            # Skip other notifications/responses

        if not got_diagnostics and not errors:
            errors.append(f"No diagnostics received within {timeout}s")

        # Send shutdown
        proc.stdin.write(encode_lsp({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "shutdown",
            "params": None,
        }))
        proc.stdin.flush()

        # Send exit
        proc.stdin.write(encode_lsp({
            "jsonrpc": "2.0",
            "method": "exit",
            "params": None,
        }))
        proc.stdin.flush()

    except BrokenPipeError:
        errors.append("lean --server process died unexpectedly")
    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if stderr_lines:
        # Filter out info-level messages, report actual errors
        real_errors = [l for l in stderr_lines if "error" in l.lower()]
        if real_errors:
            for l in real_errors[:3]:
                errors.append(f"stderr: {l}")

    return errors


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <lean_bin_dir> <lean_path> <project_file>")
        sys.exit(1)

    lean_bin = Path(sys.argv[1])
    lean_path = sys.argv[2]
    project_file = Path(sys.argv[3])

    timeout = 60
    if "--timeout" in sys.argv:
        idx = sys.argv.index("--timeout")
        if idx + 1 < len(sys.argv):
            timeout = int(sys.argv[idx + 1])

    print(f"Testing LSP with {project_file.name}...")
    errors = test_lsp(lean_bin, lean_path, project_file, timeout=timeout)

    if errors:
        print(f"FAILED: {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("OK: LSP test passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
