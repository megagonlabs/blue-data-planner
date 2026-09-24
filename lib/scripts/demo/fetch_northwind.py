"""Copy blue-data's Northwind seed and descriptions into data/demo/."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DEMO_FILE_NAMES = ("northwind_sqlite.sql", "northwind_descriptions.json")


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
            raise FileNotFoundError(f"{data_dir} has no {file_name}")
        destination_path = destination_dir / file_name
        shutil.copyfile(source_path, destination_path)
        written.append(destination_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Copy blue-data's Northwind seed and descriptions out of the blue-data checkout "
            "beside this repository."
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/demo"))
    parser.add_argument("--blue-data-data-dir", type=Path, default=Path("../blue-data/data"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    already_present = all((data_dir / name).is_file() for name in DEMO_FILE_NAMES)
    if already_present and not args.force:
        print(f"skip: {', '.join(DEMO_FILE_NAMES)} already present in {data_dir}")
        return

    written = copy_demo_files(args.blue_data_data_dir, data_dir)
    for path in written:
        print(f"  {path}")
    print(f"Wrote {len(written)} files to {data_dir}")


if __name__ == "__main__":
    main()
