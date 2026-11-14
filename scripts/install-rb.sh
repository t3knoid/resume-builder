#!/bin/sh
# Installs the `rb` command for macOS and Linux (and Git Bash on Windows): a small wrapper that
# runs rb inside the resume-builder container, from any folder, with no docker command to type.
#
#   sh scripts/install-rb.sh               install, or update after moving this folder
#   sh scripts/install-rb.sh --uninstall   remove it
#
# The wrapper goes in ~/.local/bin (set RB_BIN_DIR to choose another folder). It runs
#   docker compose --progress quiet --project-directory <this folder> run --rm app rb <arguments>
# so your .env and compose.yaml apply wherever you are. Docker must be installed and running.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BIN_DIR=${RB_BIN_DIR:-"$HOME/.local/bin"}
TARGET="$BIN_DIR/rb"
MARKER="resume-builder: runs rb inside the app container"

ours() { [ -f "$1" ] && grep -q "$MARKER" "$1"; }

if [ "${1:-}" = "--uninstall" ]; then
  if ours "$TARGET"; then
    rm -f "$TARGET"
    echo "Removed $TARGET."
  else
    echo "No resume-builder rb command found at $TARGET."
  fi
  exit 0
fi

if [ ! -f "$ROOT/compose.yaml" ]; then
  echo "error: no compose.yaml in $ROOT. Run this script from a resume-builder folder." >&2
  exit 1
fi
if [ -e "$TARGET" ] && ! ours "$TARGET"; then
  echo "error: $TARGET already exists and isn't from resume-builder, so it was left alone." >&2
  echo "       Set RB_BIN_DIR to install somewhere else, for example: RB_BIN_DIR=~/bin sh scripts/install-rb.sh" >&2
  exit 1
fi
command -v docker >/dev/null 2>&1 || echo "warning: docker isn't on your PATH yet. Install Docker before using rb." >&2

mkdir -p "$BIN_DIR"
cat > "$TARGET" <<EOF
#!/bin/sh
# $MARKER. Installed by scripts/install-rb.sh.
exec docker compose --progress quiet --project-directory "$ROOT" -f "$ROOT/compose.yaml" run --rm app rb "\$@"
EOF
chmod +x "$TARGET"
echo "Installed $TARGET (uses $ROOT)."

case ":$PATH:" in
  *":$BIN_DIR:"*)
    echo "Ready: try 'rb --help'."
    ;;
  *)
    case "$(basename "${SHELL:-sh}")" in
      zsh) RC="$HOME/.zshrc" ;;
      bash) RC="$HOME/.bashrc" ;;
      *) RC="$HOME/.profile" ;;
    esac
    LINE="export PATH=\"$BIN_DIR:\$PATH\"  # added by resume-builder install-rb.sh"
    if ! grep -qF "$LINE" "$RC" 2>/dev/null; then
      printf '\n%s\n' "$LINE" >> "$RC"
      echo "Added $BIN_DIR to your PATH in $RC."
    fi
    echo "Open a new terminal (or run: . $RC), then try 'rb --help'."
    ;;
esac
