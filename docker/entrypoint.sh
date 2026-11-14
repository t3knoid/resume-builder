#!/bin/sh
# Starts as root only long enough to make the database volume writable, then drops to the
# host user's id (PUID/PGID), so files written to bind mounts belong to that user (DK3).
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p /db /data /output /backups
  # Only the named volume is chowned. Bind mounts already belong to the host user.
  chown "$PUID:$PGID" /db
  # Give the host user a name inside the container, so `docker compose exec -u rb app bash`
  # works and the prompt doesn't read "I have no name!". Container-local; nothing on the host changes.
  if ! getent passwd "$PUID" >/dev/null; then
    getent group "$PGID" >/dev/null || groupadd -g "$PGID" rb
    getent passwd rb >/dev/null || useradd -o -u "$PUID" -g "$PGID" -M -d /tmp -s /bin/bash rb
  fi
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$0" "$@"
fi
exec "$@"
