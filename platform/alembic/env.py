import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text
from alembic import context

# Load .env from repo root before any module-level env reads.
# DATABASE_URL is read by db.py at import time, so this must happen first.
# Use pathlib for reliable resolution regardless of CWD.
import pathlib as _pathlib
_env_file = _pathlib.Path(__file__).resolve().parent.parent.parent / ".env"
if _env_file.exists():
    with open(_env_file, encoding="utf-8", errors="ignore") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from src.storage.models import Base
from src.storage.db import DEFAULT_DB_URL, _add_ssl_if_remote

config = context.config

# Only call fileConfig when the ini file contains a proper [loggers] section.
# Alembic's skeleton ini uses [logging] which is NOT the stdlib logging.config
# format — calling fileConfig on it raises KeyError: 'formatters'.
if config.config_file_name:
    import configparser as _cp
    _ini = _cp.ConfigParser()
    _ini.read(config.config_file_name)
    if _ini.has_section("loggers"):
        fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Always resolve the real DB URL from the environment.
# Read directly from os.environ (already populated from .env above) rather
# than from DEFAULT_DB_URL, which is a module-level constant in db.py that
# could have been evaluated before the .env load if the module was cached.
_db_url = _add_ssl_if_remote(
    os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo")
)


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    # Always override with the env-resolved URL — never use the ini placeholder.
    configuration["sqlalchemy.url"] = _db_url

    # Force IPv4 for Supabase connections in Fargate — Fargate VPCs often
    # lack IPv6 routing, but Supabase DNS returns an IPv6 address first.
    # psycopg2's `hostaddr` parameter bypasses DNS entirely and forces the
    # TCP connection to use the specified IP address (host= is still used for
    # SSL certificate validation).
    import socket as _socket
    from urllib.parse import urlparse as _urlparse, urlunparse as _urlunparse, \
        parse_qs as _parse_qs, urlencode as _urlencode

    _parsed = _urlparse(_db_url)
    _host = _parsed.hostname or ""
    _is_remote_host = _host not in ("localhost", "127.0.0.1", "::1", "")

    _migration_url = _db_url
    if _is_remote_host:
        # Resolve hostname — try IPv4 first, fall back to whatever we get
        try:
            _results = _socket.getaddrinfo(_host, 5432, _socket.AF_INET, _socket.SOCK_STREAM)
            _ipv4 = _results[0][4][0]  # first IPv4 address
        except (_socket.gaierror, IndexError):
            try:
                # No IPv4 available — try any address family
                _results = _socket.getaddrinfo(_host, 5432, _socket.AF_UNSPEC, _socket.SOCK_STREAM)
                _ipv4 = _results[0][4][0]
            except (_socket.gaierror, IndexError):
                _ipv4 = None

        if _ipv4:
            _params = _parse_qs(_parsed.query, keep_blank_values=True)
            _params["hostaddr"] = [_ipv4]
            _params.setdefault("sslmode", ["require"])
            _params.setdefault("gssencmode", ["disable"])
            _new_query = _urlencode({k: v[0] for k, v in _params.items()})
            _migration_url = _urlunparse(_parsed._replace(query=_new_query))
            print(f"alembic/env.py: resolved {_host} -> {_ipv4} (IPv4 forced via hostaddr)")

    configuration["sqlalchemy.url"] = _migration_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        connection.commit()

        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
