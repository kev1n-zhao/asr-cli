#!/usr/bin/env bash

set -euo pipefail

MIN_PYTHON="3.12"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
VENV_DIR="$REPO_DIR/.venv"
USER_BIN_DIR="${HOME}/.local/bin"
LAUNCHER_PATH="${USER_BIN_DIR}/asr-cli"

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    fail "This installer only supports macOS."
  fi
}

require_apple_silicon() {
  if [[ "$(uname -m)" != "arm64" ]]; then
    fail "asr-cli uses MLX and requires Apple Silicon (arm64) on macOS."
  fi
}

python_meets_requirement() {
  local python_bin="$1"
  "$python_bin" -c "import sys; raise SystemExit(0 if sys.version_info >= tuple(map(int, \"$MIN_PYTHON\".split('.'))) else 1)"
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_meets_requirement "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

print_python_install_help() {
  cat <<EOF
Python ${MIN_PYTHON} or newer is required before installing asr-cli.

Install Python first, then rerun this script. Common options on macOS:
  1. Homebrew: brew install python@${MIN_PYTHON}
  2. Official installer: https://www.python.org/downloads/macos/
EOF
}

ensure_ffmpeg() {
  if command -v ffmpeg >/dev/null 2>&1; then
    log "ffmpeg already installed."
    return 0
  fi

  if command -v brew >/dev/null 2>&1; then
    log "Installing ffmpeg with Homebrew..."
    brew install ffmpeg
    return 0
  fi

  cat <<'EOF'
ffmpeg is not installed.

asr-cli can still be installed now, but some media formats require ffmpeg for decoding.
Install ffmpeg later with one of:
  brew install ffmpeg
  https://ffmpeg.org/download.html
EOF
}

install_python_package() {
  local python_bin="$1"

  if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating virtual environment in $VENV_DIR"
    "$python_bin" -m venv "$VENV_DIR"
  else
    log "Reusing existing virtual environment in $VENV_DIR"
  fi

  log "Upgrading pip tooling in repo virtual environment..."
  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

  log "Installing asr-cli into repo virtual environment..."
  "$VENV_DIR/bin/pip" install -e "$REPO_DIR"
}

install_launcher() {
  mkdir -p "$USER_BIN_DIR"

  cat >"$LAUNCHER_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$VENV_DIR/bin/asr-cli" "\$@"
EOF

  chmod +x "$LAUNCHER_PATH"
  log "Installed launcher at $LAUNCHER_PATH"
}

print_path_hint() {
  case ":$PATH:" in
    *":$USER_BIN_DIR:"*)
      log "You can now run: asr-cli --help"
      ;;
    *)
      cat <<EOF
Installation finished.

The CLI launcher was installed to:
  $LAUNCHER_PATH

Add this directory to your shell PATH, then restart your shell:
  export PATH="$USER_BIN_DIR:\$PATH"

After that, run:
  asr-cli --help
EOF
      ;;
  esac
}

main() {
  require_macos
  require_apple_silicon

  local python_bin
  if ! python_bin="$(find_python)"; then
    print_python_install_help
    exit 1
  fi

  log "Using Python: $python_bin"
  log "Python version: $("$python_bin" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

  ensure_ffmpeg
  install_python_package "$python_bin"
  install_launcher
  print_path_hint
}

main "$@"
