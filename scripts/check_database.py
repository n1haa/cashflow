"""Read-only MySQL connection and migration check: python -m scripts.check_database."""
import sys

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend.config import Settings
from backend.database import make_engine


def main():
    engine = make_engine(Settings.from_env().database_url)
    try:
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT VERSION()"))
            tables = set(inspect(connection).get_table_names())
            if not {"users", "user_sessions", "alembic_version"} <= tables:
                print("MySQL connected; run: python -m alembic upgrade head")
                return 1
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0001_users_sessions":
                print("MySQL connected; unexpected migration revision. Run: python -m alembic upgrade head")
                return 1
            print(f"MySQL {version}: connection OK; users and sessions migration OK.")
            return 0
    except SQLAlchemyError:
        print("MySQL connection failed. Check MYSQL_* in .env, server availability and user grants.")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
