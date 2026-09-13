#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-$ROOT/.venv}
OKX_CLI_SPEC=${OKX_CLI_SPEC:-@okx_ai/okx-trade-cli@^1.4.4}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: Python 3 is required" >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: Node.js 18+ is required for OKX CLI" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm is required for OKX CLI" >&2; exit 1; }

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -r "$ROOT/requirements.txt"

if command -v okx >/dev/null 2>&1; then
  echo "OKX CLI already installed: $(okx --version 2>/dev/null | sed -n '1p')"
else
  echo "Installing official OKX CLI: $OKX_CLI_SPEC"
  npm install -g "$OKX_CLI_SPEC"
fi

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/env.example" "$ROOT/.env"
fi
chmod 600 "$ROOT/.env"
chmod +x "$ROOT/scripts/okxquant_okx_setup.py"

cat <<EOF

OKXQuant dependencies installed.
Next:
  1. Edit $ROOT/.env and keep OKXQUANT_OKX_ENV=demo initially.
  2. Configure OKX using ONE method:
     - Recommended standalone path: enter a DEMO API Key in /admin.
     - CLI OAuth path: run OAuth login as the SAME Linux user that runs both services.
  3. Verify without placing an order:
     $VENV_DIR/bin/python $ROOT/scripts/okxquant_okx_setup.py

OAuth site must be explicitly selected: global / eea / us / tr.
Do not copy another user's ~/.okx directory or commit credentials.
EOF
