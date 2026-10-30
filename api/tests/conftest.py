import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from fleck_api.config import get_settings
from fleck_api.db import make_engine


@pytest.fixture
def db_url():
    """Throwaway database migrated with `alembic upgrade head`, dropped afterwards."""
    base = get_settings().database_url.rsplit("/", 1)[0]
    name = f"fleck_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base + "/postgres", autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    url = f"{base}/{name}"
    cfg = Config(attributes={"url": url})
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.upgrade(cfg, "head")
    yield url
    with psycopg.connect(base + "/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def make_session(db_url):
    engine = make_engine(db_url)
    yield sessionmaker(engine)
    engine.dispose()
