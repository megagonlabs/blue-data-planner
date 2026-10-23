"""Build a blue-data registry over TAG-Bench's BIRD databases into data/tag_bench/registry.json."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)
from blue_data.registry.enrichment.metadata import (  # pyright: ignore[reportMissingTypeStubs]
    MetaData,
)
from blue_data.source.json.file_json_source import (  # pyright: ignore[reportMissingTypeStubs]
    FileJSONSource,
)
from blue_data.source.json.memory_json_source import (  # pyright: ignore[reportMissingTypeStubs]
    MemoryJSONSource,
)
from blue_data.source.sql.sqlite.sqlite_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteDBSource,
)

from tests.benchmarks.tag_bench.paths import DEFAULT_DATA_DIR
from tests.benchmarks.tag_bench.reasoning_effort import effective_reasoning_effort

SOURCE_NAME = "tag_bench"
DEFAULT_SAMPLE_LIMIT = 10
DEFAULT_MODEL_ENV_VAR = "BLUE_DATA_OPS_MODEL"
DEFAULT_REASONING_EFFORT = "none"

# One source holding all five databases, so reaching an unnamed source is a search with
# several candidates rather than one. SQLite has no schemas, so blue-data reports the
# collection as "public" and the path reads source -> database -> public -> entity -> attribute.


def sqlite_database_names(databases_dir: Path) -> list[str]:
    """Return the names of the .db files in databases_dir, sorted, without the extension.

    Raises:
        FileNotFoundError: If databases_dir does not exist.
        ValueError: If databases_dir holds no .db file.
    """
    if not databases_dir.is_dir():
        raise FileNotFoundError(
            f"Missing {databases_dir}. Run fetch_databases.py first to populate it."
        )
    names = sorted(path.stem for path in databases_dir.glob("*.db"))
    if not names:
        raise ValueError(
            f"No .db files in {databases_dir}. Run fetch_databases.py first to populate it."
        )
    return names


def build_registry(
    databases_dir: Path,
    source_name: str = SOURCE_NAME,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    llm_fn: Callable[[str], str] | None = None,
) -> Any:
    """Register databases_dir as one SQLite source, then sync, collect stats, and enrich it."""
    # An absolute directory: blue-data resolves the stored path against the working
    # directory, and the registry is reopened from lib/ by the harness and from the
    # repository root by the CLI. The registry file is a local build artifact, so it may
    # carry a machine-specific path.
    registry: Any = DataRegistry(name=source_name.upper(), store=MemoryJSONSource())
    source = SQLiteDBSource(
        name=source_name,
        properties={"connection": {"database_directory": str(databases_dir.resolve())}},
    )
    registry.register_source(source)
    registry.sync_source(source_name, recursive=True)
    registry.collect_source_stats(source_name, recursive=True, sample_limit=sample_limit)
    if llm_fn is None:
        registry.enrich_source(source_name)
    else:
        registry.metadata_enricher = MetaData(llm_fn=llm_fn)
        registry.enrich_source_metadata(source_name, recursive=True)
    return registry


def entity_counts_by_database(registry: Any, source_name: str = SOURCE_NAME) -> dict[str, int]:
    """Return how many entity records each of source_name's databases holds."""
    counts: dict[str, int] = {}
    for database_record in registry.list_source_databases(source_name):
        database = str(database_record["name"])
        entity_count = 0
        for collection_record in registry.list_database_collections(source_name, database):
            entity_count += len(
                registry.list_collection_entities(
                    source_name, database, str(collection_record["name"])
                )
            )
        counts[database] = entity_count
    return counts


def check_every_database_registered(
    registry: Any, expected_db_names: list[str], source_name: str = SOURCE_NAME
) -> None:
    """Raise unless every expected database synced with at least one entity.

    Raises:
        ValueError: If a database is absent from the registry or holds no entity record.
    """
    counts = entity_counts_by_database(registry, source_name)
    absent = [name for name in expected_db_names if name not in counts]
    if absent:
        raise ValueError(
            f"Databases {absent} are on disk but absent from the registry after syncing"
        )
    without_entities = [name for name in expected_db_names if counts[name] == 0]
    if without_entities:
        raise ValueError(f"Databases {without_entities} synced with no entity records")


def write_registry(registry: Any, registry_path: Path) -> None:
    """Write blue-data's standard dictionary store for the CLI/API/MCP and planner."""
    # Serialized once at the end: a file-backed store re-reads and rewrites the whole
    # document on every record write, which is thousands of rewrites during a sync.
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    store: Any = FileJSONSource(properties={"connection": {"path": str(registry_path)}})
    store.set("$", registry.store.get("$"))


def llm_client_properties(model: str, reasoning_effort: str | None) -> dict[str, Any]:
    """Return the properties an enrichment LLMClient takes, given an already-resolved effort.

    reasoning_effort is None where the parameter is left out of the call entirely; any other
    value, "none" included, is sent to the provider as openai.reasoning_effort.
    """
    properties: dict[str, Any] = {
        "service_prefix": "openai",
        "openai.model": model,
        # No input_template: an enrichment prompt carries sample values, and
        # templating would substitute into them.
        "input_json": '[{"role": "user", "content": ""}]',
        "input_context": "$[0]",
        "input_context_field": "content",
        "input_field": "messages",
        "output_path": "$.choices[0].message.content",
    }
    if reasoning_effort is not None:
        properties["openai.reasoning_effort"] = reasoning_effort
    return properties


def build_llm_fn(model: str, reasoning_effort: str | None) -> Callable[[str], str]:
    """Return a (prompt) -> text callable over model, verified by one probe call.

    Raises:
        ValueError: If the probe call returns no text.
    """
    from blue_data_planner.llm import LLMClient

    client = LLMClient(
        name="tag_bench_enricher",
        properties=llm_client_properties(model, reasoning_effort),
    )

    def call_model(prompt: str) -> str:
        return str(client.execute_api_call(prompt))

    # blue-data's enricher catches every exception from this callable and logs a warning,
    # so a misconfigured provider would otherwise produce a registry silently missing
    # every description. Probe once, before the enrichment runs.
    probe = call_model("Reply with the single word: ready.")
    if not probe.strip():
        raise ValueError(f"Model {model!r} returned no text for the probe call")
    return call_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a blue-data registry over BIRD databases")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--source-name", default=SOURCE_NAME)
    parser.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Add model-written descriptions and richer semantic types, one call per attribute.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get(DEFAULT_MODEL_ENV_VAR),
        help=f"Model for --describe. Defaults to ${DEFAULT_MODEL_ENV_VAR}.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=DEFAULT_REASONING_EFFORT,
        help=(
            "Reasoning effort for --describe's model. 'off' omits the parameter, for a model "
            f"that rejects it. Default: {DEFAULT_REASONING_EFFORT}."
        ),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    registry_path: Path = args.data_dir / "registry.json"
    if registry_path.exists() and not args.force:
        print(f"skip {registry_path} (exists; use --force to rebuild)")
        return

    databases_dir: Path = args.data_dir / "databases"
    db_names = sqlite_database_names(databases_dir)

    llm_fn: Callable[[str], str] | None = None
    if args.describe:
        if not args.model:
            raise ValueError(f"--describe needs --model or ${DEFAULT_MODEL_ENV_VAR}")
        llm_fn = build_llm_fn(
            str(args.model), effective_reasoning_effort(str(args.reasoning_effort))
        )

    print(f"registering {len(db_names)} databases from {databases_dir}: {', '.join(db_names)}")
    registry = build_registry(
        databases_dir,
        source_name=args.source_name,
        sample_limit=args.sample_limit,
        llm_fn=llm_fn,
    )
    check_every_database_registered(registry, db_names, source_name=args.source_name)
    write_registry(registry, registry_path)

    counts = entity_counts_by_database(registry, args.source_name)
    for database, entity_count in sorted(counts.items()):
        print(f"  {database}: {entity_count} entities")
    size_mb = registry_path.stat().st_size / 1_000_000
    print(f"Wrote {registry_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
