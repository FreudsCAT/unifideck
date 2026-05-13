#!/usr/bin/env bash
# Unifideck Plugin Build Script — new-architecture branch
# Reflects the 5-layer package restructure (v0.7+)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_LOCATION="$SCRIPT_DIR/cli"
OUTPUT_DIR="$SCRIPT_DIR/out"

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Argument parsing ─────────────────────────────────────────
ENV_MODE="${1:-dev}"
INSTALL_AFTER="${2:-}"
PACKAGE_VERSION=$(grep '"version"' "$SCRIPT_DIR/package.json" | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')

if [[ "$ENV_MODE" == "prod" ]]; then
    VERSION_TAG="v$PACKAGE_VERSION"
    ZIP_NAME="unifideck.prod.$VERSION_TAG.zip"
    PLUGIN_VERSION="$PACKAGE_VERSION"
    log_info "Building in PRODUCTION mode ($VERSION_TAG)"
elif [[ "$ENV_MODE" == "dev" ]]; then
    mkdir -p "$OUTPUT_DIR"
    LATEST_DEV=$(ls -1 "$OUTPUT_DIR"/unifideck.dev.v*.zip 2>/dev/null | \
        sed 's/.*unifideck\.dev\.v\([0-9]*\)\.zip/\1/' | sort -n | tail -1)
    DEV_VER=$([ -z "$LATEST_DEV" ] && echo 1 || echo $((LATEST_DEV + 1)))
    VERSION_TAG="v$DEV_VER"
    ZIP_NAME="unifideck.dev.$VERSION_TAG.zip"
    PLUGIN_VERSION="$PACKAGE_VERSION-dev$DEV_VER"
    log_info "Building in DEVELOPMENT mode ($VERSION_TAG)"
else
    log_error "Unknown mode: $ENV_MODE. Use 'dev' or 'prod'."
    exit 1
fi

OUTPUT_FILE="$OUTPUT_DIR/$ZIP_NAME"

echo "========================================="
echo "Unifideck Plugin Build Script (v0.7+)"
echo "========================================="
echo "Mode:   $ENV_MODE"
echo "Target: $OUTPUT_FILE"
echo ""

# ── Binary versions (sourced from package.json remote_binary) ─
# These must stay in sync with package.json "remote_binary" entries.
LEGENDARY_URL="https://github.com/Heroic-Games-Launcher/legendary/releases/download/0.20.38/legendary_linux_x86_64"
GOGDL_URL="https://github.com/Heroic-Games-Launcher/heroic-gogdl/releases/download/v1.1.2/gogdl_linux_x86_64"
NILE_URL="https://github.com/imLinguin/nile/releases/download/v1.1.2/nile_linux_x86_64"
COMET_URL="https://github.com/imLinguin/comet/releases/download/v0.3.2/comet-x86_64-unknown-linux-gnu"
WINETRICKS_URL="https://raw.githubusercontent.com/Winetricks/winetricks/20260125/src/winetricks"

# ── Pre-build: download/verify bundled binaries ───────────────
prebuild_binaries() {
    log_info "Running pre-build binary checks..."

    _download_bin() {
        local name="$1" url="$2" dest="$3" validate_cmd="$4"
        log_info "Checking $name..."
        if curl -sL "$url" -o "$dest.new"; then
            chmod +x "$dest.new"
            if eval "$validate_cmd" > /dev/null 2>&1; then
                mv "$dest.new" "$dest"
                log_success "$name downloaded/verified"
            else
                rm -f "$dest.new"
                log_warn "$name: downloaded binary failed validation, keeping existing"
            fi
        else
            log_warn "$name: download failed, keeping existing"
        fi
    }

    _download_bin "legendary" "$LEGENDARY_URL" "$SCRIPT_DIR/bin/legendary" \
        '"$SCRIPT_DIR/bin/legendary.new" --version'

    _download_bin "gogdl" "$GOGDL_URL" "$SCRIPT_DIR/bin/gogdl" \
        '"$SCRIPT_DIR/bin/gogdl.new" --version --auth-config-path /dev/null'

    _download_bin "nile" "$NILE_URL" "$SCRIPT_DIR/bin/nile" \
        '"$SCRIPT_DIR/bin/nile.new" --version'

    _download_bin "comet" "$COMET_URL" "$SCRIPT_DIR/bin/comet" \
        '"$SCRIPT_DIR/bin/comet.new" --version'

    # Winetricks is a shell script — validate by grepping a known string
    log_info "Checking winetricks..."
    if curl -sL "$WINETRICKS_URL" -o "$SCRIPT_DIR/bin/winetricks.new"; then
        chmod +x "$SCRIPT_DIR/bin/winetricks.new"
        if grep -q "WINETRICKS_VERSION" "$SCRIPT_DIR/bin/winetricks.new"; then
            mv "$SCRIPT_DIR/bin/winetricks.new" "$SCRIPT_DIR/bin/winetricks"
            log_success "winetricks downloaded/verified"
        else
            rm -f "$SCRIPT_DIR/bin/winetricks.new"
            log_warn "winetricks: downloaded file invalid, keeping existing"
        fi
    else
        log_warn "winetricks: download failed, keeping existing"
    fi

    echo ""
}

# ── Pre-build: requirements check ────────────────────────────
check_requirements() {
    if [ ! -f "$SCRIPT_DIR/requirements.txt" ] && [ -f "$SCRIPT_DIR/requirements.in" ]; then
        log_info "requirements.txt missing — copying from requirements.in..."
        cp "$SCRIPT_DIR/requirements.in" "$SCRIPT_DIR/requirements.txt"
        log_success "Created requirements.txt"
    elif [ ! -f "$SCRIPT_DIR/requirements.txt" ]; then
        log_warn "requirements.txt missing and requirements.in not found!"
    fi
}

# ── Pre-build: generate src/i18n/locales.generated.ts ────────
gen_locales() {
    log_info "Generating src/i18n/locales.generated.ts..."
    if cd "$SCRIPT_DIR/scripts" && python3 gen_locale_imports.py \
        --config "$SCRIPT_DIR/defaults/config.json" \
        --output "$SCRIPT_DIR/src/i18n/locales.generated.ts" 2>&1; then
        cd "$SCRIPT_DIR"
        log_success "locales.generated.ts ready"
    else
        cd "$SCRIPT_DIR"
        log_warn "Locale generation failed — build may fail if file is missing"
    fi
}

# ── Pre-build: read version from plugin.json ─────────────────
sync_version() {
    PLUGIN_VERSION=$(grep '"version"' "$SCRIPT_DIR/plugin.json" | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')
    log_info "Plugin version (plugin.json): $PLUGIN_VERSION"
    echo ""
}

# ── Decky CLI detection ───────────────────────────────────────
get_decky_cli_url() {
    local os arch base="https://github.com/SteamDeckHomebrew/cli/releases/latest/download"
    case "$(uname -s)" in Linux*) os="linux";; Darwin*) os="darwin";; CYGWIN*|MINGW*|MSYS*) os="windows";; *) os="linux";; esac
    case "$(uname -m)" in x86_64|amd64) arch="x64";; arm64|aarch64) arch="arm64";; *) arch="x64";; esac
    if [ "$os" = "windows" ]; then echo "${base}/decky-${os}-${arch}.exe"
    else echo "${base}/decky-${os}-${arch}.tar.gz"; fi
}

check_decky_cli() {
    local cli="$CLI_LOCATION/decky"
    if test -f "$cli" && "$cli" --version > /dev/null 2>&1; then return 0; fi
    if test -f "$cli"; then
        log_warn "Decky CLI incompatible with this platform — re-downloading..."
        rm -f "$cli"
    fi
    log_info "Downloading Decky CLI for $(uname -s)/$(uname -m)..."
    local url; url=$(get_decky_cli_url)
    mkdir -p "$CLI_LOCATION"
    if [[ "$url" == *.tar.gz ]]; then
        if curl -sL "$url" -o "$CLI_LOCATION/decky.tar.gz"; then
            cd "$CLI_LOCATION"; tar -xzf decky.tar.gz 2>/dev/null; rm -f decky.tar.gz
            chmod +x decky 2>/dev/null || true; cd "$SCRIPT_DIR"
            test -f "$cli" && "$cli" --version > /dev/null 2>&1 && { log_success "Decky CLI ready"; return 0; }
        fi
    fi
    log_warn "Could not download Decky CLI — will use local build"
    return 1
}

check_container_engine() {
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then echo "docker"; return 0; fi
    if command -v podman &>/dev/null && podman info &>/dev/null 2>&1; then echo "podman"; return 0; fi
    return 1
}

# ── Staging directory contents ───────────────────────────────
#   Mirrors the exact runtime layout expected by Decky Loader.
#   Directories included (relative to repo root):
#     py_modules/   — vendored deps + unifideck 5-layer package
#     bin/          — native binaries + shell wrappers (no .py scripts)
#     defaults/     — config.json schema + backend defaults
#     src/          — TypeScript source (built into dist/ by Decky CLI)
#     assets/       — plugin artwork/icons
#   Files included:
#     main.py, plugin.json, package.json, pnpm-lock.yaml,
#     tsconfig.json, rollup.config.mjs, requirements.txt,
#     LICENSE, README.md
_stage_plugin_files() {
    local dest="$1"
    mkdir -p "$dest"

    for dir in py_modules bin defaults src; do
        [ -d "$SCRIPT_DIR/$dir" ] && cp -r "$SCRIPT_DIR/$dir" "$dest/"
    done
    cp -r "$SCRIPT_DIR/assets" "$dest/" 2>/dev/null || true

    for f in main.py plugin.json package.json pnpm-lock.yaml tsconfig.json \
              rollup.config.mjs requirements.txt LICENSE README.md; do
        [ -f "$SCRIPT_DIR/$f" ] && cp "$SCRIPT_DIR/$f" "$dest/"
    done
}

# ── Build with Decky CLI (Docker/Podman) ─────────────────────
build_with_cli() {
    local engine="$1"
    log_info "Building with Decky CLI using $engine..."
    rm -f "$OUTPUT_FILE"

    # Clean dist (may be owned by root from previous container build)
    if [ -d "$SCRIPT_DIR/dist" ]; then
        log_info "Cleaning dist/..."
        rm -rf "$SCRIPT_DIR/dist" 2>/dev/null || \
            "$engine" run --rm -v "$SCRIPT_DIR":/v -w /v alpine rm -rf dist
    fi

    local staging; staging=$(mktemp -d)
    local staging_plugin="$staging/unifideck-staging"
    _stage_plugin_files "$staging_plugin"
    chmod -R a+rX "$staging_plugin" 2>/dev/null || true

    mkdir -p "$OUTPUT_DIR"
    "$CLI_LOCATION/decky" plugin build "$staging_plugin" \
        --output-path "$OUTPUT_DIR" \
        --engine "$engine" \
        --follow-symlinks \
        --build-as-root

    rm -rf "$staging"

    local expected="$OUTPUT_DIR/Unifideck.zip"
    if [ -f "$expected" ] && [ "$expected" != "$OUTPUT_FILE" ]; then
        mv "$expected" "$OUTPUT_FILE"
        log_success "Renamed Unifideck.zip → $ZIP_NAME"
    elif [ -f "$OUTPUT_FILE" ]; then
        log_success "Build output at $ZIP_NAME"
    else
        log_warn "Expected CLI output not found: Unifideck.zip"
    fi
    log_success "CLI build complete: $OUTPUT_FILE"
}

# ── Local build (Steam Deck / no container fallback) ─────────
build_local() {
    log_info "Building locally (no container engine)..."

    cd "$SCRIPT_DIR"
    log_info "Compiling TypeScript frontend..."
    if ! pnpm run build; then log_error "Frontend compilation failed"; exit 1; fi
    log_success "Frontend compiled"

    mkdir -p "$OUTPUT_DIR"
    local build_dir; build_dir=$(mktemp -d)
    local plugin_dir="$build_dir/Unifideck"
    _stage_plugin_files "$plugin_dir"

    # Also copy the compiled frontend output
    [ -d "$SCRIPT_DIR/dist" ] && cp -r "$SCRIPT_DIR/dist" "$plugin_dir/"

    # ── Critical file verification ───────────────────────────
    log_info "Verifying critical files..."
    local CRITICAL_FILES=(
        # Plugin root
        "main.py"
        "plugin.json"
        "dist/index.js"

        # Layer 1 — core/types (pure data island)
        "py_modules/unifideck/core/types/__init__.py"
        "py_modules/unifideck/core/types/domain.py"
        "py_modules/unifideck/core/types/events.py"
        "py_modules/unifideck/core/types/results.py"

        # Layer 2 — core infrastructure
        "py_modules/unifideck/core/__init__.py"
        "py_modules/unifideck/core/cache_manager.py"
        "py_modules/unifideck/core/sync_service.py"
        "py_modules/unifideck/core/manifest.py"
        "py_modules/unifideck/core/paths.py"
        "py_modules/unifideck/core/exe_finder.py"
        "py_modules/unifideck/core/metrics_collector.py"
        "py_modules/unifideck/core/io/__init__.py"
        "py_modules/unifideck/core/io/async_file_ops.py"
        "py_modules/unifideck/core/io/safe_file_op.py"
        "py_modules/unifideck/core/binaries/__init__.py"
        "py_modules/unifideck/core/binaries/binary_resolver.py"
        "py_modules/unifideck/core/binaries/binary_signatures.py"
        "py_modules/unifideck/core/binaries/cli_timeouts.py"

        # EventBus
        "py_modules/unifideck/event_bus/__init__.py"
        "py_modules/unifideck/event_bus/event_bus.py"
        "py_modules/unifideck/event_bus/priority_dispatcher.py"
        "py_modules/unifideck/event_bus/event_replay.py"
        "py_modules/unifideck/event_bus/event_bus_extensions.py"
        "py_modules/unifideck/event_bus/bus_pipeline.py"

        # Config
        "py_modules/unifideck/config/__init__.py"
        "py_modules/unifideck/config/config_manager.py"
        "py_modules/unifideck/config/schema.json"
        "py_modules/unifideck/config/validator.py"
        "py_modules/unifideck/config/startup.py"

        # Bootstrap
        "py_modules/unifideck/bootstrap/boot.py"
        "py_modules/unifideck/bootstrap/teardown.py"
        "py_modules/unifideck/bootstrap/pipeline_factory.py"
        "py_modules/unifideck/bootstrap/cache_registry.py"

        # Layer 6 — RPC mixins
        "py_modules/unifideck/rpc/__init__.py"
        "py_modules/unifideck/rpc/mixins/store.py"
        "py_modules/unifideck/rpc/mixins/sync.py"
        "py_modules/unifideck/rpc/mixins/download.py"
        "py_modules/unifideck/rpc/mixins/launch.py"
        "py_modules/unifideck/rpc/mixins/playtime.py"
        "py_modules/unifideck/rpc/mixins/security.py"
        "py_modules/unifideck/rpc/mixins/observability.py"
        "py_modules/unifideck/rpc/mixins/action.py"
        "py_modules/unifideck/rpc/mixins/cloud_failure.py"
        "py_modules/unifideck/rpc/mixins/config_validation.py"
        "py_modules/unifideck/rpc/mixins/ui.py"

        # Layer 4 — Store connectors
        "py_modules/unifideck/stores/__init__.py"
        "py_modules/unifideck/stores/epic/__init__.py"
        "py_modules/unifideck/stores/epic/store.py"
        "py_modules/unifideck/stores/gog/__init__.py"
        "py_modules/unifideck/stores/gog/store.py"
        "py_modules/unifideck/stores/amazon/__init__.py"
        "py_modules/unifideck/stores/amazon/amazon_store.py"
        "py_modules/unifideck/stores/ubisoft/__init__.py"
        "py_modules/unifideck/stores/ubisoft/store.py"
        "py_modules/unifideck/stores/microsoft/__init__.py"
        "py_modules/unifideck/stores/microsoft/microsoft_store.py"

        # Layer 5 — Services
        "py_modules/unifideck/services/__init__.py"
        "py_modules/unifideck/services/download/service.py"
        "py_modules/unifideck/services/playtime/service.py"
        "py_modules/unifideck/services/cloud_save/service.py"
        "py_modules/unifideck/services/shortcut/service.py"
        "py_modules/unifideck/services/artwork/service.py"
        "py_modules/unifideck/services/launcher/service.py"
        "py_modules/unifideck/services/security/service.py"
        "py_modules/unifideck/services/bootstrap/service_defs.py"
        "py_modules/unifideck/services/bootstrap/container.py"
        "py_modules/unifideck/services/metadata_service.py"
        "py_modules/unifideck/services/account_service.py"
        "py_modules/unifideck/services/proton_service.py"

        # Support packages
        "py_modules/unifideck/auth/__init__.py"
        "py_modules/unifideck/auth/browser.py"
        "py_modules/unifideck/auth/orchestrator.py"
        "py_modules/unifideck/steam/__init__.py"
        "py_modules/unifideck/steam/library.py"
        "py_modules/unifideck/steam/shortcuts.py"
        "py_modules/unifideck/steam/steamgriddb.py"
        "py_modules/unifideck/cdp/__init__.py"
        "py_modules/unifideck/compatibility/__init__.py"
        "py_modules/unifideck/compatibility/library.py"
        "py_modules/unifideck/compatibility/proton_helpers.py"
        "py_modules/unifideck/security/__init__.py"
        "py_modules/unifideck/security/secure_token_store.py"
        "py_modules/unifideck/security/ephemeral_creds.py"
        "py_modules/unifideck/metadata/__init__.py"
        "py_modules/unifideck/metadata/metacritic.py"
        "py_modules/unifideck/metadata/unifidb.py"
        "py_modules/unifideck/utils/__init__.py"
        "py_modules/unifideck/utils/paths.py"
        "py_modules/unifideck/utils/locale.py"
        "py_modules/unifideck/launcher/__init__.py"
        "py_modules/unifideck/launcher/dispatcher.py"
        "py_modules/unifideck/actions/__init__.py"
        "py_modules/unifideck/actions/dispatch.py"

        # Vendored third-party deps
        "py_modules/vdf/__init__.py"
        "py_modules/websockets/__init__.py"
        "py_modules/aiohttp/__init__.py"
        "py_modules/certifi/__init__.py"

        # Native binaries
        "bin/legendary"
        "bin/gogdl"
        "bin/nile"
        "bin/comet"
        "bin/winetricks"
        "bin/unifideck-launcher"
        "bin/unifideck-runner"
        "bin/EpicGamesLauncher.exe"
        "bin/stubs/GalaxyCommunication.exe"
        "bin/umu/umu/umu-run"

        # Defaults
        "defaults/config.json"
    )

    local missing=0
    for f in "${CRITICAL_FILES[@]}"; do
        if [ ! -e "$plugin_dir/$f" ]; then
            log_error "Missing critical file: $f"
            missing=$((missing + 1))
        fi
    done
    if [ "$missing" -gt 0 ]; then
        log_error "$missing critical file(s) missing — aborting"
        rm -rf "$build_dir"
        exit 1
    fi
    log_success "All critical files present"

    # Set executable bits on binaries
    find "$plugin_dir/bin" -type f -exec chmod +x {} \; 2>/dev/null || true

    # Sanity-check plugin.json
    grep -q '"api_version"' "$plugin_dir/plugin.json" || \
        log_warn "plugin.json missing api_version — frontend may fail to load!"

    # Package
    log_info "Creating zip package..."
    cd "$build_dir"
    zip -r "$OUTPUT_FILE" Unifideck \
        -x "Unifideck/.git/*" \
        -x "Unifideck/**/__pycache__/*" \
        -x "Unifideck/**/*.pyc" \
        -x "Unifideck/node_modules/*" \
        -x "Unifideck/tests/*" \
        -x "Unifideck/.gitignore" \
        -x "Unifideck/decky.pyi" \
        -x "Unifideck/*.backup" \
        -x "Unifideck/vc_redist.x64.exe" \
        -x "Unifideck/antigravity-dashboard/*" \
        -q

    cd "$SCRIPT_DIR"
    rm -rf "$build_dir"

    local size; size=$(ls -lh "$OUTPUT_FILE" | awk '{print $5}')
    echo ""
    echo "========================================="
    log_success "Build Complete!"
    echo "========================================="
    echo "Mode:    $ENV_MODE"
    echo "Package: $OUTPUT_FILE"
    echo "Version: $PLUGIN_VERSION"
    echo "Size:    $size"
    echo ""
    echo "To install:"
    echo "  QAM → Decky → Settings → Developer → Install from ZIP"
    echo "========================================="
}

# ── Install to Decky plugins dir ──────────────────────────────
install_plugin() {
    local plugins_dir="$HOME/homebrew/plugins"
    local install_dir="$plugins_dir/Unifideck"

    [ -d "$plugins_dir" ] || { log_error "Decky plugins dir not found: $plugins_dir"; return 1; }
    [ -f "$OUTPUT_FILE" ] || { log_error "Build output not found: $OUTPUT_FILE"; return 1; }

    echo ""
    echo "========================================="
    echo "Installing Plugin"
    echo "========================================="
    log_info "Stopping Decky plugin loader..."
    sudo systemctl stop plugin_loader 2>/dev/null || true
    sleep 1

    if [ -d "$install_dir" ]; then
        log_info "Removing existing installation..."
        sudo rm -rf "$install_dir"
    fi

    log_info "Extracting to $plugins_dir..."
    cd "$plugins_dir" && unzip -qo "$OUTPUT_FILE" && cd "$SCRIPT_DIR"

    [ -d "$install_dir" ] || { log_error "Extraction failed"; sudo systemctl start plugin_loader; return 1; }

    sudo chown -R deck:deck "$install_dir"
    chmod -R 755 "$install_dir"
    log_success "Installed to $install_dir"

    log_info "Starting Decky plugin loader..."
    sudo systemctl start plugin_loader
    log_success "Decky restarted — plugin active shortly"
    echo "========================================="
}

# ── Main ──────────────────────────────────────────────────────
main() {
    prebuild_binaries
    check_requirements
    gen_locales
    sync_version

    if check_decky_cli; then
        ENGINE=$(check_container_engine || true)
        if [ -n "$ENGINE" ]; then
            chmod -R a+rwX "$SCRIPT_DIR" || true
            build_with_cli "$ENGINE"
        else
            log_warn "No container engine available — falling back to local build"
            build_local
        fi
    else
        build_local
    fi

    [[ "$INSTALL_AFTER" == "install" ]] && install_plugin
}

main "$@"
