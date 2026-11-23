"""Restore a verified RiseGram MTProto archive into a NEW EMPTY Neon database.

This script is manual-only. It never changes an existing MTProto/ledger DSN,
never drops databases, and refuses to restore over existing application data.
After a successful test restore, freeze writers and take a fresh final backup
before any production cutover. Use a newly created Neon database, not the
existing RiseGram economy database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path("/data/risegram-db-backups")
LABEL = "mtproto-postgres-20261123"


def load_verified_archive():
    meta_path = ROOT / (LABEL + ".json")
    info = json.loads(meta_path.read_text())
    archive = ROOT / info["filename"]
    if archive.parent != ROOT or not archive.is_file():
        raise RuntimeError("Archive missing or outside backup directory")
    if archive.stat().st_size != info["bytes"]:
        raise RuntimeError("Archive size mismatch")
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    if hasher.hexdigest() != info["sha256"]:
        raise RuntimeError("Archive SHA-256 mismatch")
    return archive, info


def target_info():
    source_dsn = os.getenv("RISEGRAM_MT_PROTO_BACKUP_DSN", "")
    target_dsn = os.getenv("RISEGRAM_NEON_TARGET_DSN", "")
    if not source_dsn or not target_dsn:
        raise RuntimeError("Both source and target DSNs must be configured privately")
    source = conninfo_to_dict(source_dsn)
    target = conninfo_to_dict(target_dsn)
    host = str(target.get("host", "")).lower()
    if not host.endswith(".neon.tech"):
        raise RuntimeError("Target must be a Neon PostgreSQL hostname")
    if (str(source.get("host", "")), str(source.get("port", "5432")),
        str(source.get("dbname", ""))) == (
        str(target.get("host", "")), str(target.get("port", "5432")),
        str(target.get("dbname", ""))):
        raise RuntimeError("Source and target are the same database")
    if not target.get("dbname") or not target.get("user"):
        raise RuntimeError("Target DSN requires explicit database and user")
    return target_dsn, target


def ensure_empty(conn):
    query = """
        SELECT COUNT(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND n.nspname <> 'information_schema'
          AND n.nspname NOT LIKE 'pg_%'
    """
    count = int(conn.execute(query).fetchone()[0])
    if count:
        raise RuntimeError(
            "Target is not empty. Create another NEW Neon database; no data was changed."
        )


def client_env(info):
    env = dict(os.environ)
    # No passwords or full DSNs are passed as command-line arguments or logged.
    mapping = {
        "host": "PGHOST", "port": "PGPORT", "dbname": "PGDATABASE",
        "user": "PGUSER", "password": "PGPASSWORD", "sslmode": "PGSSLMODE",
        "channel_binding": "PGCHANNELBINDING",
    }
    for key, val in mapping.items():
        if info.get(key) is not None:
            env[val] = str(info[key])
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-to-new-empty-neon", action="store_true")
    args = parser.parse_args()
    archive, meta = load_verified_archive()
    target_dsn, info = target_info()
    print("BACKUP_PRESENT_AND_HASH_VERIFIED bytes=" + str(meta["bytes"]), flush=True)
    with psycopg.connect(target_dsn, connect_timeout=15, autocommit=True) as conn:
        ensure_empty(conn)
        print("NEON_TARGET_EMPTY_CONFIRMED", flush=True)
    if not args.restore_to_new_empty_neon:
        print("PREFLIGHT_ONLY: no changes made; restore needs explicit flag", flush=True)
        return 0
    if os.getenv("RISEGRAM_CONFIRM_NEW_NEON_RESTORE", "") != "YES_EMPTY_NEON_ONLY":
        raise RuntimeError("RESTORE_LOCKED: explicit environment confirmation missing")
    tool = shutil.which("pg_restore")
    if not tool:
        raise RuntimeError("pg_restore is unavailable")
    cmd = [tool, "--exit-on-error", "--single-transaction",
           "--no-owner", "--no-privileges", "--dbname", str(info["dbname"]),
           str(archive)]
    print("NEON_RESTORE_START: old Railway database remains untouched", flush=True)
    # Do not surface pg_restore stderr: it may contain target internal details.
    result = subprocess.run(cmd, env=client_env(info), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError("NEON_RESTORE_FAILED: target transaction rolled back")
    with psycopg.connect(target_dsn, connect_timeout=15, autocommit=True) as conn:
        rows = int(conn.execute("""
            SELECT COUNT(*) FROM pg_tables
            WHERE schemaname NOT LIKE 'pg_%'
              AND schemaname <> 'information_schema'
        """).fetchone()[0])
        size_bytes = int(conn.execute(
            "SELECT pg_database_size(current_database())"
        ).fetchone()[0])
    if rows < 1:
        raise RuntimeError("NEON_RESTORE_UNVERIFIED: no restored tables")
    print(f"NEON_RESTORE_VALIDATED tables={rows} bytes={size_bytes}", flush=True)
    print("CUTOVER_NOT_PERFORMED: take a fresh final backup with writes frozen",
          flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("MIGRATION_BLOCKED: " + str(exc), file=sys.stderr)
        sys.exit(1)
