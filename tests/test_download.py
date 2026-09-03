import json
import zipfile
from pathlib import Path

import pytest

import download


def test_lean_extraction_replaces_stale_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction = tmp_path / "lean-extract"
    stale_lake = extraction / "lean-4.31.0-linux" / "bin" / "lake"
    stale_lake.parent.mkdir(parents=True)
    stale_lake.write_bytes(b"stale")

    def fake_download(_url: str, destination: Path) -> None:
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(
                "lean-4.31.0-windows/bin/lake.exe",
                b"windows lake",
            )

    monkeypatch.setattr(download, "_download", fake_download)

    toolchain = download.download_lean_toolchain(
        "v4.31.0", "windows", tmp_path
    )

    assert toolchain == extraction / "lean-4.31.0-windows"
    assert (toolchain / "bin" / "lake.exe").is_file()


def _write_vsix(
    path: Path,
    *,
    publisher: str = "waterproof-tue",
    name: str = "waterproof",
    version: str = "1.0.0",
    files: dict[str, bytes] | None = None,
) -> None:
    package = {"publisher": publisher, "name": name, "version": version}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("extension/package.json", json.dumps(package))
        for relative, contents in (files or {}).items():
            archive.writestr(f"extension/{relative}", contents)


def test_local_waterproof_vsix_replaces_reused_destination(tmp_path: Path) -> None:
    source = tmp_path / "local.vsix"
    _write_vsix(source, files={"obsolete.js": b"old"})

    extension_dirs, version = download.install_local_waterproof_vsix(
        source, tmp_path
    )
    extension = extension_dirs[0]
    assert version == "1.0.0"
    assert (extension / "obsolete.js").is_file()

    _write_vsix(source, version="2.0.0", files={"current.js": b"new"})
    extension_dirs, version = download.install_local_waterproof_vsix(
        source, tmp_path
    )
    extension = extension_dirs[0]

    assert version == "2.0.0"
    assert not (extension / "obsolete.js").exists()
    assert (extension / "current.js").read_bytes() == b"new"
    assert source.is_file()
    assert not list(tmp_path.glob(".waterproof-local-*.vsix"))


def test_local_waterproof_vsix_rejects_wrong_extension(tmp_path: Path) -> None:
    source = tmp_path / "unrelated.vsix"
    _write_vsix(source, publisher="someone", name="unrelated")

    with pytest.raises(ValueError, match="expected waterproof-tue.waterproof"):
        download.install_local_waterproof_vsix(source, tmp_path)

    assert source.is_file()
    assert not (tmp_path / "waterproof-tue.waterproof-local").exists()
