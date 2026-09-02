"""Download BIRD's SQLite databases for TAG-Bench's queries into data/tag_bench/databases/."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path

_INNER_ZIP_ENTRY = "dev_20240627/dev_databases.zip"
BIRD_DEV_ZIP_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"


def load_required_db_names(queries_path: Path) -> list[str]:
    """Return the distinct db_name values queries_path's queries need, sorted.

    Raises:
        FileNotFoundError: If queries_path does not exist.
    """
    if not queries_path.is_file():
        raise FileNotFoundError(
            f"Missing {queries_path}. Run fetch_queries.py first to produce it."
        )
    document = json.loads(queries_path.read_text())
    names = {str(record["db_name"]) for record in document["queries"]}
    return sorted(names)


def extract_bird_databases(dev_zip_path: Path, db_names: list[str], output_dir: Path) -> None:
    """Extract each db_name's .sqlite file from a BIRD dev.zip into output_dir/<db_name>.db.

    Raises:
        ValueError: If dev_zip_path's inner archive lacks a .sqlite entry for one of db_names.
    """
    with zipfile.ZipFile(dev_zip_path) as outer:
        inner_bytes = outer.read(_INNER_ZIP_ENTRY)

    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
        names_in_zip = set(inner.namelist())
        for db_name in db_names:
            entry = f"dev_databases/{db_name}/{db_name}.sqlite"
            if entry not in names_in_zip:
                raise ValueError(f"Expected {entry} in {_INNER_ZIP_ENTRY}, but it was not found")
            (output_dir / f"{db_name}.db").write_bytes(inner.read(entry))


def download_bird_zip(destination: Path) -> None:
    """Download BIRD's dev.zip to destination."""
    with urllib.request.urlopen(BIRD_DEV_ZIP_URL) as response:
        destination.write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and stage BIRD databases for TAG-Bench")
    parser.add_argument("--data-dir", type=Path, default=Path("data/tag_bench"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    queries_path: Path = args.data_dir / "queries.json"
    db_names = load_required_db_names(queries_path)
    databases_dir = args.data_dir / "databases"

    already_present = all((databases_dir / f"{name}.db").is_file() for name in db_names)
    if already_present and not args.force:
        print(f"skip: all {len(db_names)} databases already present in {databases_dir}")
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / "dev.zip"
        print(f"downloading {BIRD_DEV_ZIP_URL}")
        download_bird_zip(zip_path)
        extract_bird_databases(zip_path, db_names, databases_dir)
    print(f"Wrote {len(db_names)} databases to {databases_dir}")


if __name__ == "__main__":
    main()
