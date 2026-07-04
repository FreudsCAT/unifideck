#!/usr/bin/env bash
# .vscode/build.sh — Unifideck plugin build wrapper.
#
# prepends a call to ensure_executable_bits.py
# before the Decky CLI zip step, so the dispatcher.py shebang
# file goes out with the executable bit set.
#
# The runtime self-heal in service_bootstrap is the fallback
# for installs that lost the bits during an intermediate archive
# step, but setting them at build time means the first launch
# after a clean install Just Works without needing the
# self-heal to fire.
set -euo pipefail

CLI_LOCATION="$(pwd)/cli"
PLUGIN_ROOT="$(pwd)"

echo "Building plugin in ${PLUGIN_ROOT}"

# — ensure launcher entry points are executable
# before the CLI zips them. Idempotent, ~10ms, safe.
if [ -f "${PLUGIN_ROOT}/scripts/ensure_executable_bits.py" ]; then
 echo "[build] Running executable-bit hook..."
 python3 "${PLUGIN_ROOT}/scripts/ensure_executable_bits.py" \
 "${PLUGIN_ROOT}"
else
 echo "[build] Warning: ensure_executable_bits.py not found, skipping"
fi

printf "Please input sudo password to proceed.\n"

# read -s sudopass
# printf "\n"

echo "${sudopass:-}" | sudo -E "${CLI_LOCATION}/decky" plugin build "${PLUGIN_ROOT}"
