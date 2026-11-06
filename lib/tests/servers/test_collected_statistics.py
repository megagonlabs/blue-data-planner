"""A source's collected statistics, read back off a real server.

blue-data writes `most_common_vals` differently for each source, and only a real server produces
what it writes. Excluded from testpaths, so it runs only when named. Each test creates what it
reads and removes it afterwards, and skips unless its server's variable names a server and its
driver extra of blue-data is installed:

    MYSQL_PORT=3306 MYSQL_PASSWORD=... PGPORT=5432 PGPASSWORD=... \
    NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=... \
    uv run pytest tests/servers

MYSQL_HOST, MYSQL_USER, PGHOST, PGUSER, and NEO4J_USER are read too, with the usual defaults.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any, cast
from urllib.parse import urlsplit

import pytest

from blue_data_planner.data import DataCatalog, DataRegistry
from blue_data_planner.types.catalog import AttributeView


def the_variable_naming_a_server(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set")
    return value


def a_scratch_name() -> str:
    return f"planner{uuid.uuid4().hex[:12]}"


def the_view_after_collecting_statistics(source: Any, term: str, attribute: str) -> AttributeView:
    """Sync the source, collect its statistics, and read one attribute back through a search."""
    registry: Any = DataRegistry(name="test", rebuild=True)
    registry.register_source(source)
    registry.sync_source(source.name, recursive=True)
    registry.collect_source_stats(source.name, recursive=True, sample_limit=20)

    matches = DataCatalog(cast("DataRegistry", registry)).search_records(term)

    match = next(one for one in matches if one.name == term)
    return next(one for one in match.attributes if one.ref.attribute == attribute)


@pytest.fixture
def mysql_source() -> Iterator[Any]:
    """A MySQL source over a scratch database holding an orders table."""
    port = int(the_variable_naming_a_server("MYSQL_PORT"))
    connector: Any = pytest.importorskip("mysql.connector")
    from blue_data.source.sql.mysql.mysql_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
        MySQLDBSource,  # blue-data ships no py.typed
    )

    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = a_scratch_name()
    connection = connector.connect(
        host=host, port=port, user=user, password=password, autocommit=True
    )
    cursor = connection.cursor()
    cursor.execute(f"CREATE DATABASE {database}")
    try:
        cursor.execute(f"CREATE TABLE {database}.orders (id INT PRIMARY KEY, country VARCHAR(40))")
        cursor.execute(
            f"INSERT INTO {database}.orders VALUES (1,'France'),(2,'France'),(3,'Japan')"
        )
        yield MySQLDBSource(
            name="my",
            properties={
                "connection": {
                    "host": host,
                    "port": port,
                    "user": user,
                    "password_env": "MYSQL_PASSWORD",
                    "database": database,
                }
            },
        )
    finally:
        cursor.execute(f"DROP DATABASE {database}")
        connection.close()


@pytest.fixture
def postgres_source() -> Iterator[Any]:
    """A PostgreSQL source over a scratch database holding an analyzed orders table."""
    port = int(the_variable_naming_a_server("PGPORT"))
    psycopg2: Any = pytest.importorskip("psycopg2")
    from blue_data.source.sql.postgres.postgres_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
        PostgresDBSource,  # blue-data ships no py.typed
    )

    host = os.environ.get("PGHOST", "127.0.0.1")
    user = os.environ.get("PGUSER", "postgres")
    password = os.environ.get("PGPASSWORD", "")
    database = a_scratch_name()
    server = {"host": host, "port": port, "user": user, "password": password}
    admin = psycopg2.connect(dbname="postgres", **server)
    admin.autocommit = True
    admin_cursor = admin.cursor()
    admin_cursor.execute(f"CREATE DATABASE {database}")
    try:
        connection = psycopg2.connect(dbname=database, **server)
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE orders (id INT PRIMARY KEY, country VARCHAR(40))")
        cursor.execute("INSERT INTO orders VALUES (1,'France'),(2,'France'),(3,'Japan')")
        # ANALYZE is what fills pg_stats; a table never analyzed records no frequent values.
        cursor.execute("ANALYZE orders")
        connection.close()
        yield PostgresDBSource(
            name="pg",
            properties={
                "connection": {
                    "host": host,
                    "port": port,
                    "user": user,
                    "password_env": "PGPASSWORD",
                    "database": database,
                }
            },
        )
    finally:
        admin_cursor.execute(f"DROP DATABASE {database} WITH (FORCE)")
        admin.close()


@pytest.fixture
def neo4j_label() -> Iterator[tuple[Any, str]]:
    """A Neo4j source, and the scratch label two nodes carry in it."""
    address = urlsplit(the_variable_naming_a_server("NEO4J_URI"))
    neo4j: Any = pytest.importorskip("neo4j")
    from blue_data.source.graph.neo4j.neo4j_graph_source import (  # pyright: ignore[reportMissingTypeStubs]
        NEO4JSource,  # blue-data ships no py.typed
    )

    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    label = a_scratch_name()
    driver = neo4j.GraphDatabase.driver(address.geturl(), auth=(user, password))
    try:
        driver.execute_query(f"CREATE (:{label} {{city: 'Reno'}}), (:{label} {{city: 'Reno'}})")
        source = NEO4JSource(
            "neo",
            properties={
                "connection": {
                    "host": address.hostname,
                    "port": address.port,
                    "protocol": address.scheme,
                    "user": user,
                    "password_env": "NEO4J_PASSWORD",
                }
            },
        )
        yield source, label
    finally:
        driver.execute_query(f"MATCH (node:{label}) DETACH DELETE node")
        driver.close()


def test_a_mysql_source_stays_readable_once_its_statistics_are_collected(
    mysql_source: Any,
) -> None:
    """A search matching it still lists the table, with the column's sampled values."""
    view = the_view_after_collecting_statistics(mysql_source, "orders", "country")

    assert "France" in view.sample_values


def test_an_analyzed_postgres_source_stays_readable_once_its_statistics_are_collected(
    postgres_source: Any,
) -> None:
    """A search matching it still lists the table, with the column's sampled values."""
    view = the_view_after_collecting_statistics(postgres_source, "orders", "country")

    assert "France" in view.sample_values


def test_a_neo4j_source_stays_readable_once_its_statistics_are_collected(
    neo4j_label: tuple[Any, str],
) -> None:
    """A search matching it still lists the label, with the property's sampled values."""
    source, label = neo4j_label

    view = the_view_after_collecting_statistics(source, label, "city")

    assert "Reno" in view.sample_values
