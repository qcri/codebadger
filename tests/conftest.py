import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


@pytest.fixture
def isolated_pg_dsn():
    dsn = os.getenv("CODEBADGER_TEST_PG_DSN")
    if not dsn:
        pytest.skip("set CODEBADGER_TEST_PG_DSN to run Postgres tests")

    schema = f"codebadger_test_{uuid4().hex}"
    identifier = sql.Identifier(schema)
    options = conninfo_to_dict(dsn).get("options", "")
    test_dsn = make_conninfo(dsn, options=f"{options} -csearch_path={schema}".strip())
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(identifier))
        try:
            yield test_dsn
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(identifier))
