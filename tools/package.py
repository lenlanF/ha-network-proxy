"""Build a source-only local HA install bundle; exclude test state and binaries."""

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.3.2"


def main():
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"ha-network-proxy-{VERSION}.zip"
    files = []
    for directory in ("addons", "custom_components", "tests", "tools", ".github"):
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )
    files.extend(
        ROOT / name
        for name in (
            "README.md",
            "ARCHITECTURE.md",
            "TESTING.md",
            "RELEASE_NOTES.md",
            "repository.yaml",
            "requirements-dev.txt",
            ".gitignore",
        )
    )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(files):
            bundle.write(
                path,
                (
                    Path(f"ha-network-proxy-{VERSION}") / path.relative_to(ROOT)
                ).as_posix(),
            )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii"
    )
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert all(
            ".venv" not in name and ".tools" not in name and "sources.json" not in name
            for name in bundle.namelist()
        )
    print(f"Bundle: {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
