"""One-shot read-only backups to RiseGram existing /data volume."""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import re
import psycopg

log = logging.getLogger("risegram.backup")
ROOT = Path("/data/risegram-db-backups")
MIN_FREE = 100 * 1024 * 1024
MAX_PG = 315 * 1024 * 1024

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()

def already_verified(label):
    try:
        info = json.loads((ROOT / (label + ".json")).read_text())
        p = ROOT / info["filename"]
        valid = p.is_file() and p.stat().st_size == info["bytes"] and digest(p) == info["sha256"]
        if valid:
            log.info("BACKUP_ALREADY_VERIFIED %s bytes=%d sha256=%s", label, info["bytes"], info["sha256"])
        return valid
    except (OSError, ValueError, KeyError, TypeError):
        return False

def manifest(label, file, method):
    info = dict(filename=file.name, bytes=file.stat().st_size,
                sha256=digest(file), method=method, created_unix=int(time.time()))
    tmp = ROOT / (label + ".json.tmp")
    tmp.write_text(json.dumps(info, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, ROOT / (label + ".json"))
    log.info("BACKUP_VERIFIED %s bytes=%d sha256=%s path=%s",
             label, info["bytes"], info["sha256"], file)

def backup_sqlite():
    src_file = Path(os.getenv("DB_PATH", "/data/risegram.db"))
    label = "bot-sqlite-20261123"
    if already_verified(label):
        return
    if not src_file.is_file():
        log.warning("BACKUP_SKIP SQLite source missing")
        return
    if shutil.disk_usage(ROOT).free < MIN_FREE + src_file.stat().st_size:
        log.warning("BACKUP_SKIP insufficient SQLite backup space")
        return
    temp = ROOT / (label + ".partial")
    target = ROOT / (label + ".db")
    temp.unlink(missing_ok=True)
    try:
        src = sqlite3.connect("file:" + str(src_file) + "?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(temp))
            try:
                src.backup(dst, pages=128, sleep=0.05)
                if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("SQLite quick_check failed")
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(temp, 0o600)
        os.replace(temp, target)
        manifest(label, target, "sqlite3.backup + quick_check")
    finally:
        temp.unlink(missing_ok=True)

def backup_postgres():
    label = "mtproto-postgres-20261123"
    if already_verified(label):
        return
    dsn = os.getenv("RISEGRAM_MT_PROTO_BACKUP_DSN", "")
    if not dsn or "${{" in dsn:
        log.error("BACKUP_SKIP unresolved MTProto DSN reference")
        return
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        log.error("BACKUP_SKIP pg_dump/pg_restore not installed")
        return
    if shutil.disk_usage(ROOT).free < MIN_FREE + 20971520:
        log.error("BACKUP_SKIP backup volume full")
        return
    # Log only the major versions, not host, user, database name or credentials.
    try:
        with psycopg.connect(dsn, connect_timeout=8) as conn:
            server_major = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    except psycopg.Error as exc:
        log.error("BACKUP_FAILED source read check %s", type(exc).__name__)
        return
    tool = subprocess.run([pg_dump, "--version"], text=True,
                          capture_output=True, timeout=10)
    matches = re.search(r"(\d+)\.", tool.stdout)
    client_major = int(matches.group(1)) if matches else 0
    log.info("BACKUP_SOURCE_VERSION server=%s pg_dump=%s", server_major, client_major)
    if server_major > client_major:
        log.error("BACKUP_BLOCKED incompatible pg_dump version: client=%s source=%s",
                  client_major, server_major)
        return
    temporary = ROOT / (label + ".partial")
    target = ROOT / (label + ".dump")
    temporary.unlink(missing_ok=True)
    args = [pg_dump, "--dbname", dsn, "--format=custom", "--compress=9",
            "--no-owner", "--no-acl", "--file", str(temporary)]
    log.info("BACKUP_START %s", label)
    errfile = ROOT / (label + ".stderr.partial")
    errfile.unlink(missing_ok=True)
    try:
        # Keep diagnostic output out of service logs: PostgreSQL errors may contain
        # connection details. Only emit coarse error categories.
        with errfile.open("wb") as error_sink:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=error_sink)
            too_large = False
            while proc.poll() is None:
                if ((temporary.exists() and temporary.stat().st_size >= MAX_PG)
                        or shutil.disk_usage(ROOT).free < MIN_FREE):
                    too_large = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                time.sleep(1)
            proc.wait(timeout=30)
        if too_large:
            log.error("BACKUP_ABORT storage headroom guard")
            return
        if proc.returncode != 0:
            detail = errfile.read_text(errors="replace").lower()
            category = ("source_disk_full" if "no space left on device" in detail
                        else "authentication" if "authentication failed" in detail
                        else "connectivity" if "could not connect" in detail
                        else "permissions" if "permission denied" in detail
                        else "server_busy" if "could not serialize" in detail
                        else "other")
            log.error("BACKUP_FAILED pg_dump exit=%d category=%s",
                      proc.returncode, category)
            return
        # Decompress and read every archive block; no database is modified.
        checked = subprocess.run([pg_restore, "--file=/dev/null", str(temporary)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        if checked.returncode != 0:
            log.error("BACKUP_FAILED pg_restore archive check exit=%d", checked.returncode)
            return
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        manifest(label, target, "pg_dump custom + pg_restore full archive read")
    except (OSError, subprocess.SubprocessError):
        log.exception("BACKUP_FAILED export")
    finally:
        temporary.unlink(missing_ok=True)
        errfile.unlink(missing_ok=True)

def run():
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        os.chmod(ROOT, 0o700)
        backup_sqlite()
        backup_postgres()
    except Exception:
        log.exception("BACKUP_FAILED unexpected")

async def run_async():
    await asyncio.to_thread(run)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
