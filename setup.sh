#!/bin/sh
# Sets up resume-builder on macOS and Linux (and Git Bash on Windows): builds and starts the app,
# then installs the `rb` command. Safe to run again, for example after pulling an update.
#
#   sh setup.sh
#
# First time: it creates .env from .env.example and stops, so you can fill in your details.
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT"

command -v docker >/dev/null 2>&1 || {
  echo "error: docker isn't installed or isn't on your PATH. Install Docker, then run this again." >&2
  exit 1
}

if [ ! -f .env ]; then
  cp .env.example .env
  case $(uname -s) in
    MINGW*|MSYS*|CYGWIN*) echo "Created .env." ;;  # Windows: keep the default ids
    *)
      # Fill in your user and group ids, so files the app writes stay editable by you.
      uid=$(id -u) gid=$(id -g)
      sed -e "s/^PUID=.*/PUID=$uid/" -e "s/^PGID=.*/PGID=$gid/" .env > .env.tmp && mv .env.tmp .env
      echo "Created .env (with PUID=$uid and PGID=$gid)." ;;
  esac
  echo "Open .env, set RB_DEFAULT_USER and your other details, then run this again: sh setup.sh"
  exit 1
fi
if grep -q '^RB_DEFAULT_USER=your-name *$' .env; then
  echo "error: RB_DEFAULT_USER in .env is still 'your-name'. Set it to your folder name, then run this again." >&2
  exit 1
fi

docker compose up -d --build
sh "$ROOT/scripts/install-rb.sh"

port=$(sed -n 's/^RB_HOST_PORT=//p' .env | tr -d ' \r')
echo
echo "resume-builder is running at http://localhost:${port:-8000}"
