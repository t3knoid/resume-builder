#!/usr/bin/env python3
"""Restore a backup made by scripts/backup.py.

Works on Windows, macOS and Linux with any Python 3.8+ (standard library only):

    python scripts/restore.py                       # newest zip in ~/resume-builder-backups
    python scripts/restore.py path/to/resume-builder-backup-20261113-171214.zip
    python scripts/restore.py --user jamie-doe --files-only

What it does, after showing the plan and asking you to type `yes`:
1. Stops the app if it's running, so its file watcher can't sync half-restored files.
2. Saves the current files of each user being restored as
   backups/<user>/pre-restore-files-<date-time>.zip, so nothing is lost.
3. Replaces data/<user>/ with the copy from the backup.
4. Copies the database backup into backups/<user>/ and runs `rb restore`, which also saves the
   current database first (app-pre-restore-....db).
5. Starts the app again (its startup sync checks everything), or runs `rb sync` if it wasn't running.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup import PREFIX, ROOT, docker_compose, env_setting, host_path  # noqa: E402


def newest_zip(folder: Path) -> Path | None:
    zips = sorted(folder.glob(f"{PREFIX}*.zip"))
    return zips[-1] if zips else None


def safe_members(zf: zipfile.ZipFile) -> list[PurePosixPath]:
    """Every member path, refusing any that could land outside the target folder."""
    out = []
    for name in zf.namelist():
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
            raise SystemExit(f"error: unsafe path in the zip: {name}. Not restoring from this file.")
        out.append(p)
    return out


def extract(zf: zipfile.ZipFile, member: PurePosixPath, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(str(member)) as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)


def rb(running: bool, user: str, *args: str) -> subprocess.CompletedProcess:
    how = ["exec", "-T", "app"] if running else ["run", "--rm", "-T", "app"]
    return docker_compose(*how, "rb", "--user", user, *args)


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore resume-builder user data and databases from a backup zip.")
    ap.add_argument("zip", nargs="?", help="backup zip (default: the newest in --dest)")
    ap.add_argument("--dest", default=str(Path.home() / "resume-builder-backups"),
                    help="where to look for the newest zip when none is given (default: ~/resume-builder-backups)")
    ap.add_argument("--user", action="append", help="restore only this user (repeatable; default: every user in the zip)")
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--files-only", action="store_true", help="restore data/<user>/ only, not the database")
    what.add_argument("--db-only", action="store_true", help="restore the database only, not data/<user>/")
    ap.add_argument("--include-env", action="store_true", help="also restore .env from the zip (the current one is kept as .env.pre-restore-<date-time>)")
    ap.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    args = ap.parse_args()

    zip_path = Path(args.zip).expanduser() if args.zip else newest_zip(Path(args.dest).expanduser())
    if zip_path is None or not zip_path.is_file():
        print(f"error: no backup zip found ({args.zip or args.dest}).", file=sys.stderr)
        return 1
    data_dir = host_path(env_setting("RB_HOST_DATA", "./data"))
    backup_dir = host_path(env_setting("RB_HOST_BACKUPS", "./backups"))

    with zipfile.ZipFile(zip_path) as zf:
        members = safe_members(zf)
        in_zip = sorted({p.parts[1] for p in members if len(p.parts) >= 3 and p.parts[0] == "data" and p.parts[2] == "user.yaml"})
        users = args.user or in_zip
        missing = [u for u in users if u not in in_zip]
        if missing:
            print(f"error: not in this backup: {', '.join(missing)}. It has: {', '.join(in_zip) or 'no users'}.", file=sys.stderr)
            return 1
        dbs = {u: sorted(p for p in members if p.parts[:2] == ("backups", u) and p.suffix == ".db") for u in users}
        if not args.files_only:
            no_db = [u for u in users if not dbs[u]]
            if no_db:
                print(f"error: no database backup in the zip for {', '.join(no_db)}. Use --files-only to restore just the files.", file=sys.stderr)
                return 1
        has_env = PurePosixPath(".env") in members

        # The plan, then a confirmation.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        print(f"Restoring from {zip_path}\n")
        for u in users:
            if not args.db_only:
                current = data_dir / u
                note = f" (current files saved to backups/{u}/pre-restore-files-{stamp}.zip)" if current.exists() else ""
                print(f"  {u}: replace data/{u}/{note}")
            if not args.files_only:
                print(f"  {u}: restore the database from {dbs[u][-1].name} (current database saved first)")
        if args.include_env:
            print("  .env: " + ("replace it (current one kept as .env.pre-restore-" + stamp + ")" if has_env else "not in this backup, left alone"))
        print("\nAnything changed since this backup was made will be replaced (the copies above keep it).")
        if not args.yes:
            if not sys.stdin.isatty():
                print("error: not restoring without an answer. Run it in a terminal, or add --yes.", file=sys.stderr)
                return 1
            try:
                answer = input("Type 'yes' to restore: ")
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer.strip().lower() not in ("yes", "y"):
                print("\nNothing changed.")
                return 1

        # 1. Stop the app so its watcher doesn't sync while files and database disagree.
        docker = shutil.which("docker") is not None
        if not docker and not args.files_only:
            print("error: docker not found; it's needed to restore the database. Use --files-only, or start Docker.", file=sys.stderr)
            return 1
        was_running = docker and bool(docker_compose("ps", "--status", "running", "-q", "app").stdout.strip())
        if was_running:
            print("Stopping the app...")
            docker_compose("stop", "app")

        try:
            for u in users:
                if not args.db_only:
                    # 2. Safety copy of the current files, then 3. replace them.
                    current = data_dir / u
                    if current.exists():
                        safety = backup_dir / u / f"pre-restore-files-{stamp}.zip"
                        safety.parent.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(safety, "w", compression=zipfile.ZIP_DEFLATED) as out:
                            for path in sorted(current.rglob("*")):
                                if path.is_file():
                                    out.write(path, Path(u) / path.relative_to(current))
                        shutil.rmtree(current)
                    for p in members:
                        if p.parts[:2] == ("data", u) and not str(p).endswith("/"):
                            extract(zf, p, data_dir.joinpath(*p.parts[1:]))
                    print(f"Restored data/{u}/")
                if not args.files_only:
                    # 4. Database: copy the backup file in, then let the app restore it.
                    db = dbs[u][-1]
                    extract(zf, db, backup_dir / u / db.name)
                    result = rb(False, u, "restore", db.name)
                    if result.returncode != 0:
                        print(f"error: rb restore failed for {u}:\n{result.stdout}{result.stderr}", file=sys.stderr)
                        return 1
                    print(f"Restored the database for {u} from {db.name}")
            if args.include_env and has_env:
                env = ROOT / ".env"
                if env.exists():
                    env.rename(ROOT / f".env.pre-restore-{stamp}")
                extract(zf, PurePosixPath(".env"), env)
                print("Restored .env")
        finally:
            # 5. Start the app again; its startup sync reconciles files and database.
            if was_running:
                print("Starting the app...")
                docker_compose("start", "app")

    if docker and not was_running:
        for u in users:
            rb(False, u, "sync")
    print("Done. Check SYNC-STATUS.md in each restored folder, or http://localhost:8000.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
