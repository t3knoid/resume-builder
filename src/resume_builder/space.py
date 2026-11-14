"""The application object and per-user spaces.

`App` is shared by every interface. `UserSpace` holds one user's storage (`repo`, a
`storage.Storage`), file stores (`output`, `backups`) and the sync adapter's paths. Services
receive a UserSpace plus a RequestContext; they never open files or run SQL themselves (A1, A2).
"""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import yamlio
from .config import Settings, load_settings
from .errors import NotFound, ValidationFailed, Problem
from .models import UserSettings
from .filestore import FileStore, local_file_store
from .secretstore import EnvSecretStore, SecretStore
from .storage import Storage, sqlite_storage
from .security import LOCAL_TENANT, Policy, RequestContext
from .slug import is_slug


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


class UserSpace:
    def __init__(self, app: "App", user: str):
        if not is_slug(user):
            raise ValidationFailed(f"`{user}` is not a valid user name.", [Problem("Use lowercase letters, digits and hyphens.", example="alex-morgan")])
        self.app = app
        self.user = user
        self.settings_env = app.settings
        # The YAML/Markdown sync adapter's folder and lock (local input files, PRD 8.1).
        self.data_dir = app.settings.user_data(user)
        self.lock_path = app.settings.user_db(user).parent / "sync.lock"
        # Everything services use goes through these interfaces.
        self.repo: Storage = app.storage_factory(app, user)
        self.output: FileStore = app.file_store_factory(app, user, "output")
        self.backups: FileStore = app.file_store_factory(app, user, "backups")
        self._lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle
    def migrate(self) -> list[int]:
        return self.repo.migrate(backup_fn=self.backup)

    def close(self) -> None:
        self.repo.close()

    @property
    def secrets(self) -> SecretStore:
        return self.app.secrets

    def changed(self, kind: str, key: str | None = None, *, doc_kind: str | None = None, slug: str = "") -> bool:
        """Tell the App's change listeners that stored data changed (see changes.py).

        Returns True if any listener updated a copy the user sees (locally: a working-copy file).
        """
        from .changes import Change

        change = Change(kind, key, doc_kind, slug)
        return any([listener(self, change) for listener in self.app.change_listeners])

    # ------------------------------------------------------------ settings
    @property
    def user_settings(self) -> UserSettings:
        s = self.repo.get_settings()
        if s is None:
            return UserSettings(display_name=self.user)
        return s

    # ------------------------------------------------------------ audit and backups
    def audit(self, ctx: RequestContext, operation: str, target: str = "", result: str = "ok") -> None:
        self.repo.audit(ctx.actor, ctx.channel, ctx.request_id, operation, target, result)

    def backup(self, kind: str = "manual") -> str:
        """Back up the database to the backups store (DK6). Retention is counted per kind.

        Returns where the backup went (a path locally).
        """
        keep = 30
        try:
            if self.repo.schema_version() > 0:
                keep = self.user_settings.backups.keep
        except Exception:
            pass
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        where = self.backups.put(f"app-{kind}-{stamp}.db", self.repo.dump())
        same_kind = [k for k in self.backups.list(f"app-{kind}-") if k.endswith(".db")]
        for old in same_kind[:-keep] if keep > 0 else []:
            self.backups.delete(old)
        return where

    def backup_names(self) -> list[str]:
        return [k for k in self.backups.list("app-") if k.endswith(".db") and "/" not in k]

    def daily_backup_if_due(self) -> str | None:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if self.backups.list(f"app-daily-{today}"):
            return None
        return self.backup("daily")

    # ------------------------------------------------------------ files (sync adapter helpers)
    def path(self, rel: str) -> Path:
        p = (self.data_dir / rel).resolve()
        if self.data_dir.resolve() not in p.parents and p != self.data_dir.resolve():
            raise NotFound(f"`{rel}` is outside the user's folder.")
        return p

    def write_back_yaml(self, rel: str, plain: dict, template: str | None = None, force: bool = False,
                        file_plain: dict | None = None) -> bool:
        """Write current data back to a YAML file (R7.12) unless the user has unsynced edits there.

        Returns True if the file was written. When the user has pending edits, the next sync
        merges them three-way instead (R7.11).
        """
        p = self.path(rel)
        if not force and not self._safe_to_write(rel, p):
            return False
        yamlio.write_yaml(p, file_plain if file_plain is not None else plain, template_text=self._template_text(template, rel))
        self.repo.put_sync_file(rel, file_hash(p), plain, "applied", [])
        return True

    def write_back_text(self, rel: str, text: str, stored: str, force: bool = False) -> bool:
        """Write a Markdown working copy. `stored` is what the database holds (header stripped)."""
        p = self.path(rel)
        if not force and not self._safe_to_write(rel, p):
            return False
        yamlio.write_text(p, text)
        self.repo.put_sync_file(rel, file_hash(p), {"text": stored}, "applied", [])
        return True

    def _safe_to_write(self, rel: str, p: Path) -> bool:
        if not p.exists():
            return True
        row = self.repo.sync_file(rel)
        return bool(row and row["hash"] == file_hash(p))

    def _template_text(self, template: str | None, rel: str) -> str | None:
        if not template:
            return None
        t = self.app.settings.seed_dir / template
        if not t.exists():
            return None
        depth = rel.count("/")
        prefix = "../" * depth
        return render_seed(t.read_text(encoding="utf-8"), {"schema_prefix": prefix})


def render_seed(text: str, values: dict) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


class App:
    """Everything one host process shares: settings, the operation registry, the policy, plugins.

    Build it with `build_app()` (CLI, server, MCP) or `resume_builder.web.app.create_app()`, which
    an outside host uses too. Nothing is created at import time.
    """

    def __init__(self, settings: Settings | None = None, *, registry=None, policy: Policy | None = None,
                 plugins: tuple | list = (), load_entry_points: bool = False, tenant_id: str = LOCAL_TENANT,
                 template_dirs: tuple | list = (), document_template_dirs: tuple | list = (),
                 storage_factory=None, file_store_factory=None, secrets: SecretStore | None = None,
                 change_listeners: list | None = None):
        from .operations import core_registry
        from .plugins import Extensions, apply_plugin, discover_plugins

        self.settings = settings or load_settings()
        self.tenant_id = tenant_id
        self.policy = policy  # None: the process-wide policy (security.set_policy)
        self.storage_factory = storage_factory or sqlite_storage  # (app, user_id) -> Storage
        self.file_store_factory = file_store_factory or local_file_store  # (app, user_id, area) -> FileStore
        self.secrets: SecretStore = secrets or EnvSecretStore()
        if change_listeners is None:  # locally: write changes back to the user's YAML/Markdown files
            from .sync_ops import write_back
            change_listeners = [write_back]
        self.change_listeners: list = list(change_listeners)
        self.registry = (registry or core_registry()).copy()
        self.extensions = Extensions(self.registry)
        self._spaces: dict[str, UserSpace] = {}
        self._lock = threading.Lock()
        if load_entry_points:
            for name, plugin in discover_plugins():
                apply_plugin(self.extensions, plugin, name)
        for plugin in plugins:
            apply_plugin(self.extensions, plugin)
        # Directories the caller passes win over plugin directories, which win over the package.
        for d in reversed(list(template_dirs)):
            self.extensions.add_template_dir(d)
        for d in reversed(list(document_template_dirs)):
            self.extensions.add_document_templates(d)

    def use(self, plugin, name: str | None = None) -> None:
        """Apply one more plugin to this App."""
        from .plugins import apply_plugin
        apply_plugin(self.extensions, plugin, name)

    def space(self, user: str) -> UserSpace:
        with self._lock:
            sp = self._spaces.get(user)
            if sp is None:
                sp = UserSpace(self, user)
                sp.migrate()
                self._spaces[user] = sp
            return sp

    def discover_users(self) -> tuple[list[str], list[str]]:
        """User spaces are folders under the data root containing user.yaml (R10.3)."""
        users, problems = [], []
        root = self.settings.data_root
        if not root.exists():
            return users, problems
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            if not is_slug(d.name):
                problems.append(f"`{d.name}`: folder name must be lowercase letters, digits and hyphens. Rename it to use it as a user.")
            elif not (d / "user.yaml").exists():
                problems.append(f"`{d.name}`: no user.yaml, so it is not a user folder. Run `init-user {d.name}` to set it up.")
            else:
                users.append(d.name)
        return users, problems

    def close(self) -> None:
        for sp in self._spaces.values():
            sp.close()
        self._spaces.clear()


def build_app(settings: Settings | None = None, *, load_entry_points: bool | None = None, **kwargs) -> App:
    """The one way the CLI, the server and the MCP server build their App (and an outside host can too).

    Installed plugins (entry point group `resume_builder.plugins`) load unless RB_PLUGINS=off.
    Other keyword arguments go to `App`.
    """
    if load_entry_points is None:
        load_entry_points = os.environ.get("RB_PLUGINS", "on").lower() not in ("off", "0", "false", "no")
    return App(settings, load_entry_points=load_entry_points, **kwargs)
