#!/usr/bin/env bash
# Convenience wrapper: keeps the uv venv OUT of this host-mounted repo and runs the CLI.
# Usage: ./run.sh run            # full refresh: fetch -> model -> optimize -> render
#        ./run.sh run --no-fetch # re-model/render from cached data
#        ./run.sh --help
set -euo pipefail
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-${SANDBOX_VM_STATE:-$HOME/.local/state/sandbox-vm}/fifa-wc-2026/venv}"
# NixOS: manylinux wheels (numpy/scipy) need libstdc++/libgomp/libz on the loader path.
export LD_LIBRARY_PATH="$HOME/.nix-profile/lib:${LD_LIBRARY_PATH:-}"
# Keep Python bytecode cache OUT of this host-mounted repo.
export PYTHONPYCACHEPREFIX="${SANDBOX_VM_STATE:-$HOME/.local/state/sandbox-vm}/fifa-wc-2026/pycache"
cd "$(dirname "$0")"
exec uv run python -m src.cli "$@"
