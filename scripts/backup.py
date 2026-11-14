#!/usr/bin/env python3
"""Back up everything resume-builder keeps outside git into one zip file.

Works on Windows, macOS and Linux with any Python 3.8+ (standard library only):

    python scripts/backup.py
    python scripts/backup.py --dest D:/Backups/resume-builder --keep 20
    python scripts/backup.py --include-env --all-backups

What it does:
1. Asks the app for a fresh database backup (`rb backup`) for every user. The database lives in
   the Docker volume rb-db, not in a folder, so this is the only way to capture it.
2. Zips your data folder, the newest database backup per user and, with --include-env, .env.
3. Keeps the newest --keep zips in the destination and deletes older ones.

output/ is left out: every export can be rebuilt from its snapshot (`rb regenerate`).
Files are copied as they are; the script never reads or changes their contents.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "resume-builder-backup-"

RESTORE = """resume-builder backup, {when}
Users: {users}

To restore on a resume-builder checkout:
1. Copy data/<user>/ back into the data folder.
2. Copy backups/<user>/*.db into the backups folder.
3. docker compose up -d
4. docker compose run --rm app rb --user <user> restore <file name of the .db>
5. docker compose run --rm app rb --user <user> sync
"""


def env_setting(name: str, default: str) -> str:
    """A value from .env if set there, else the compose.yaml default."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip():
                return value.strip().strip("'\"")
    return default


def host_path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


def docker_compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True)


def fresh_db_backups(users: list[str]) -> None:
    if shutil.which("docker") is None:
        print("warning: docker not found; using the newest existing database backups.", file=sys.stderr)
        return
    running = bool(docker_compose("ps", "--status", "running", "-q", "app").stdout.strip())
    for user in users:
        print(f"Backing up the database for {user}...")
        how = ["exec", "-T", "app"] if running else ["run", "--rm", "-T", "app"]
        result = docker_compose(*how, "rb", "--user", user, "backup")
        if result.returncode != 0:
            print(f"warning: rb backup failed for {user}; using the newest existing backup.\n{result.stderr.strip()}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Back up resume-builder user data and databases into a zip file.")
    ap.add_argument("--dest", default=str(Path.home() / "resume-builder-backups"),
                    help="folder for the zip; pick one that is itself backed up or synced (default: ~/resume-builder-backups)")
    ap.add_argument("--keep", type=int, default=10, help="how many zips to keep in --dest (default: 10)")
    ap.add_argument("--include-env", action="store_true",
                    help="also include .env, which holds your contact details: only use a destination you trust")
    ap.add_argument("--all-backups", action="store_true", help="include every database backup, not just the newest per user")
    ap.add_argument("--skip-db-backup", action="store_true", help="don't ask the app for a fresh database backup first")
    args = ap.parse_args()

    data_dir = host_path(env_setting("RB_HOST_DATA", "./data"))
    backup_dir = host_path(env_setting("RB_HOST_BACKUPS", "./backups"))
    if not data_dir.is_dir():
        print(f"error: no data folder at {data_dir}. Run this from a resume-builder checkout.", file=sys.stderr)
        return 1
    users = sorted(p.name for p in data_dir.iterdir() if (p / "user.yaml").is_file())
    if not users:
        print(f"error: no user folders (with a user.yaml) in {data_dir}.", file=sys.stderr)
        return 1

    if not args.skip_db_backup:
        fresh_db_backups(users)

    dest = Path(args.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    zip_path = dest / f"{PREFIX}{now:%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(data_dir.rglob("*")):
            if path.is_file():
                zf.write(path, Path("data") / path.relative_to(data_dir))
        for user in users:
            dbs = sorted((backup_dir / user).glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not dbs:
                print(f"warning: no database backups for {user} in {backup_dir / user}.", file=sys.stderr)
            for db in dbs if args.all_backups else dbs[:1]:
                zf.write(db, Path("backups") / user / db.name)
        env = ROOT / ".env"
        if args.include_env and env.is_file():
            zf.write(env, ".env")
        zf.writestr("RESTORE.txt", RESTORE.format(when=f"{now:%Y-%m-%d %H:%M:%S}", users=", ".join(users)))

    for old in sorted(dest.glob(f"{PREFIX}*.zip"), reverse=True)[max(args.keep, 1):]:
        old.unlink()

    print(f"Backup written: {zip_path}")
    if not args.include_env:
        print(".env was not included (add --include-env to include it; it holds your contact details).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
