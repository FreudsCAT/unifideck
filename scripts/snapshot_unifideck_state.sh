#!/usr/bin/env bash
# Snapshots Unifideck's persistent state for before/after comparison across a version
# switch. Never touches game install directories. Only ever creates a new archive --
# never deletes or modifies anything live.
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "Usage: $0 DEST_DIR LABEL" >&2
  echo "  DEST_DIR  existing directory to write the archive into (e.g. an SD/USB mount)" >&2
  echo "  LABEL     short tag, e.g. pre-0.7-baseline / post-0.7-round1" >&2
  exit 1
fi

DEST_DIR="$1"
LABEL="$2"

if [ ! -d "$DEST_DIR" ]; then
  echo "error: DEST_DIR does not exist: $DEST_DIR" >&2
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BASENAME="unifideck-snapshot-${LABEL}-${TIMESTAMP}"
ARCHIVE_PATH="${DEST_DIR%/}/${BASENAME}.tar.gz"
MANIFEST_PATH="${DEST_DIR%/}/${BASENAME}.manifest.txt"

STAGING_DIR="/tmp/unifideck-snapshot-staging"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR/steam"
trap 'rm -rf "$STAGING_DIR"' EXIT

# Resolve the live Steam root the same way the plugin does: symlink target, falling
# back to the standard native install location.
STEAM_ROOT="$(readlink -f "$HOME/.steam/steam" 2>/dev/null || true)"
if [ -z "$STEAM_ROOT" ] || [ ! -d "$STEAM_ROOT" ]; then
  STEAM_ROOT="$HOME/.local/share/Steam"
fi

# Only the Steam files Unifideck actually writes -- not the rest of the Steam install.
if [ -f "$STEAM_ROOT/config/config.vdf" ]; then
  mkdir -p "$STAGING_DIR/steam/config"
  cp "$STEAM_ROOT/config/config.vdf" "$STAGING_DIR/steam/config/"
fi
if [ -d "$STEAM_ROOT/userdata" ]; then
  for userdir in "$STEAM_ROOT"/userdata/*/; do
    uid="$(basename "$userdir")"
    config_dir="${userdir}config"
    [ -d "$config_dir" ] || continue
    mkdir -p "$STAGING_DIR/steam/userdata/$uid/config"
    # Only what Unifideck itself writes -- not Steam's own shaderhitcache/librarycache,
    # nor any pre-existing manual shortcuts.vdf.backup_* files from unrelated debugging.
    [ -f "$config_dir/shortcuts.vdf" ] && cp "$config_dir/shortcuts.vdf" "$STAGING_DIR/steam/userdata/$uid/config/"
    [ -f "$config_dir/localconfig.vdf" ] && cp "$config_dir/localconfig.vdf" "$STAGING_DIR/steam/userdata/$uid/config/"
    [ -d "$config_dir/grid" ] && cp -a "$config_dir/grid" "$STAGING_DIR/steam/userdata/$uid/config/"
  done
fi

PATHS=()
MANIFEST_LINES=()

add_path() {
  local path="$1"
  if [ -e "$path" ]; then
    PATHS+=("$path")
    local size
    size="$(du -sh "$path" 2>/dev/null | cut -f1)"
    MANIFEST_LINES+=("${size}	${path}")
  else
    MANIFEST_LINES+=("(absent)	${path}")
  fi
}

add_path "$HOME/.local/share/unifideck"
add_path "$HOME/.config/unifideck"
add_path "$HOME/.config/legendary"
add_path "$HOME/.config/nile"
add_path "$HOME/homebrew/data/Unifideck/cache"
add_path "$STAGING_DIR/steam"

tar czf "$ARCHIVE_PATH" "${PATHS[@]}"

PLUGIN_VERSION="unknown"
if [ -f "$HOME/homebrew/plugins/Unifideck/package.json" ]; then
  PLUGIN_VERSION="$(grep -m1 '"version"' "$HOME/homebrew/plugins/Unifideck/package.json" | sed -E 's/.*"version"[^"]*"([^"]+)".*/\1/')"
fi

{
  echo "Unifideck state snapshot"
  echo "label:                   $LABEL"
  echo "timestamp:               $TIMESTAMP"
  echo "deployed plugin version: $PLUGIN_VERSION"
  echo "archive:                 $ARCHIVE_PATH"
  echo
  echo "captured paths:"
  printf '  %s\n' "${MANIFEST_LINES[@]}"
  echo
  echo "known non-regressions to keep in mind when diffing against another snapshot:"
  echo "  - homebrew/data/Unifideck/cache/ only exists from 0.7 onward (CacheManager"
  echo "    relocated derived caches there); its absence in a pre-0.7 snapshot is expected."
  echo "  - proton_settings.json changed schema (0.6.1 dict -> 0.7.1 plain string); a"
  echo "    pre-0.7 per-game Force-Compat pin normalizing to unset under 0.7 is intentional,"
  echo "    not data loss."
} > "$MANIFEST_PATH"

echo "Snapshot written:  $ARCHIVE_PATH"
echo "Manifest written:  $MANIFEST_PATH"
