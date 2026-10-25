from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_migration_upgrade_and_downgrade(tmp_path):
    config = Config("alembic.ini")
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert {"users", "user_sessions"} <= set(inspector.get_table_names())
    assert inspector.get_unique_constraints("users")[0]["column_names"] == ["email"]
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
