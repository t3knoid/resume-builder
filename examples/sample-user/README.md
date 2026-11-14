# Example user (fictional)

`alex-morgan/` is a complete, fictional user folder: same layout as a real one under `data/<user>/`. Tests and documentation use it. Nothing here describes a real person or employer.

Try it without touching your own data:

    RB_DATA_ROOT=examples/sample-user RB_DB_ROOT=/tmp/rb-demo/db RB_OUTPUT_ROOT=/tmp/rb-demo/output \
    RB_BACKUP_ROOT=/tmp/rb-demo/backups RB_DEFAULT_USER=alex-morgan rb bootstrap

(Running it writes `SYNC-STATUS.md` and `.schemas/` into the folder; both are regenerated each time.)
