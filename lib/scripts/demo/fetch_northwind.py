"""Fetch blue-data's Northwind seed and descriptions into data/demo/."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

BLUE_DATA_REPOSITORY = "ssh://git@github.com/megagon-internal/blue-data.git"
BLUE_DATA_DATA_DIRECTORY = "data"
DEMO_FILE_NAMES = ("northwind_sqlite.sql", "northwind_descriptions.json")

# SSH to megagon-internal is already required to install this package, since uv clones
# blue-data and blue-data-ops that way, so the fetch adds no prerequisite. Where only a
# token is available, the same two files come from the REST API.
GH_API_NOTE = """\
Where SSH to megagon-internal is unavailable but a GitHub token is, the same two files
come from the REST API, one call each:

  gh api repos/megagon-internal/blue-data/contents/data/northwind_sqlite.sql?ref=<rev> \\
    --jq .content | base64 -d > data/demo/northwind_sqlite.sql

<rev> is the blue-data revision this script reads out of lib/pyproject.toml. That path is
not the default because it would make the gh CLI a prerequisite of the demo.
"""


def blue_data_revision(pyproject_path: Path) -> str:
    """Return the blue-data commit pinned under [tool.uv.sources] in pyproject_path.

    Raises:
        FileNotFoundError: If pyproject_path does not exist.
        ValueError: If it declares no blue-data source, or that source pins no revision.
    """
    if not pyproject_path.is_file():
        raise FileNotFoundError(f"Missing {pyproject_path}. Run this script from the repo root.")
    document: dict[str, Any] = tomllib.loads(pyproject_path.read_text())
    sources: Any = document.get("tool", {}).get("uv", {}).get("sources", {})
    source: Any = sources.get("blue-data")
    if source is None:
        raise ValueError(f"No [tool.uv.sources] entry for blue-data in {pyproject_path}")
    revision: Any = source.get("rev")
    if not revision:
        raise ValueError(f"blue-data's [tool.uv.sources] entry in {pyproject_path} pins no rev")
    return str(revision)


def fetch_blue_data_directory(
    revision: str, checkout_dir: Path, repository: str = BLUE_DATA_REPOSITORY
) -> Path:
    """Sparse-fetch repository's data/ at revision into checkout_dir, returning that data/.

    Raises:
        subprocess.CalledProcessError: If any git command fails.
        FileNotFoundError: If the revision carries no data/ directory.
    """
    checkout_dir.mkdir(parents=True, exist_ok=True)
    run_git(["init", "--quiet"], checkout_dir)
    run_git(["remote", "add", "origin", repository], checkout_dir)
    # Set before checkout so only data/ ever lands in the working tree.
    run_git(["sparse-checkout", "set", BLUE_DATA_DATA_DIRECTORY], checkout_dir)
    run_git(["fetch", "--quiet", "--depth", "1", "origin", revision], checkout_dir)
    run_git(["checkout", "--quiet", "FETCH_HEAD"], checkout_dir)

    data_dir = checkout_dir / BLUE_DATA_DATA_DIRECTORY
    if not data_dir.is_dir():
        raise FileNotFoundError(f"blue-data at {revision} carries no {BLUE_DATA_DATA_DIRECTORY}/")
    return data_dir


def run_git(arguments: list[str], repository_dir: Path) -> None:
    """Run one git command inside repository_dir, raising on a non-zero exit."""
    subprocess.run(["git", "-C", str(repository_dir), *arguments], check=True)


def copy_demo_files(data_dir: Path, destination_dir: Path) -> list[Path]:
    """Copy the Northwind seed and descriptions out of data_dir, returning what was written.

    Raises:
        FileNotFoundError: If data_dir is missing one of the two files.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for file_name in DEMO_FILE_NAMES:
        source_path = data_dir / file_name
        if not source_path.is_file():
            raise FileNotFoundError(f"blue-data's {BLUE_DATA_DATA_DIRECTORY}/ has no {file_name}")
        destination_path = destination_dir / file_name
        shutil.copyfile(source_path, destination_path)
        written.append(destination_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch blue-data's Northwind seed and descriptions at the revision "
            "lib/pyproject.toml pins, so the demo data matches the installed package."
        ),
        epilog=GH_API_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/demo"))
    parser.add_argument("--pyproject", type=Path, default=Path("lib/pyproject.toml"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    already_present = all((data_dir / name).is_file() for name in DEMO_FILE_NAMES)
    if already_present and not args.force:
        print(f"skip: {', '.join(DEMO_FILE_NAMES)} already present in {data_dir}")
        return

    revision = blue_data_revision(args.pyproject)
    print(f"fetching {BLUE_DATA_DATA_DIRECTORY}/ from blue-data at {revision}")
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            blue_data_dir = fetch_blue_data_directory(revision, Path(tmp_dir) / "blue-data")
            written = copy_demo_files(blue_data_dir, data_dir)
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"git failed with exit code {error.returncode} while fetching {BLUE_DATA_REPOSITORY} "
            f"at {revision}.\nThis fetch needs SSH access to github.com/megagon-internal, the "
            f"same access uv needs to install this package.\n\n{GH_API_NOTE}"
        ) from error
    for path in written:
        print(f"  {path}")
    print(f"Wrote {len(written)} files to {data_dir}")


if __name__ == "__main__":
    main()
