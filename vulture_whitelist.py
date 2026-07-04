"""Vulture whitelist — pin patterns flagged as unused but actually live.

Vulture sees only the static call graph; it can't know about:

* **Decky Loader plugin protocol** — ``Plugin._main`` / ``_unload``
  / ``_uninstall`` are called by the Decky runtime, not by our
  code.
* **Bus subscribers** — ``@subscribe(Events.X)`` handlers are
  invoked via the event bus dispatcher; vulture sees no static
  call to them.
* **Dataclass / NamedTuple fields** — accessed by attribute, but
  vulture flags the class body as unused if no static
  ``obj.field`` reference exists.
* **Enum values** — ``Events.SECURITY_FOO`` may be passed as a
  bus topic by string-keyed lookup; vulture can't follow.
* **Context manager protocol** — ``__aenter__`` / ``__aexit__``
  must accept ``exc_type, exc, tb`` even when ``tb`` is unused.
* **ABC methods** — abstract bodies are intentionally empty.
* **Public API surface** — methods called by external consumers
  (frontend RPC, other plugins) have no static caller inside the
  package.

Each entry below is `name = unused_reason_when_known`. The form
``Class.method`` covers both class method and instance method.
``module.name`` covers a function or module-level binding.

Maintenance: re-run ``vulture py_modules/unifideck
--make-whitelist`` to regenerate candidate entries, then prune by
hand. Don't blanket-accept the autogen output; it includes real
dead code too.
"""

# ── Decky Loader plugin protocol ─────────────────────────────────
# main.Plugin._main           — Decky entry point on plugin load.
# main.Plugin._unload         — Decky teardown.
# main.Plugin._uninstall      — Decky uninstall hook.
# main.Plugin._migration      — Decky version migration hook.
Plugin._main  # noqa: F821
Plugin._unload  # noqa: F821
Plugin._uninstall  # noqa: F821
Plugin._migration  # noqa: F821

# ── Bootstrap entry points ───────────────────────────────────────
# bootstrap.boot.boot_plugin       — called by main.Plugin._main
# bootstrap.teardown.unload_plugin — called by main.Plugin._unload
boot_plugin  # noqa: F821
unload_plugin  # noqa: F821

# ── Context manager protocol noise ───────────────────────────────
# tb / exc_tb — required by __aexit__ signature, unused by body.
tb  # noqa: F821
exc_tb  # noqa: F821

# ── Backward-compat aliases (intentional dead code for users) ────
# config.i18n_schema.validate_i18n — alias for validate_i18n_schema
validate_i18n  # noqa: F821

# ── Public RPC / API surface ─────────────────────────────────────
# These functions are called by the frontend via Decky's RPC bridge
# (call_plugin_method), never by the Python codebase.
list_supported_verbs  # noqa: F821
resolve_user_config_path  # noqa: F821
discover_installed_games  # noqa: F821
discover_and_log  # noqa: F821
clear_store_cookies  # noqa: F821

# ── Public utility modules (cross-package consumers) ─────────────
# core/io/async_file_ops.py — public IO helpers; may be used by
# downstream plugins or by the standalone launcher.
listdir  # noqa: F821
ensure_dir  # noqa: F821
read_json  # noqa: F821
write_json  # noqa: F821
read_text  # noqa: F821
write_text  # noqa: F821

# ── ConfigManager public methods (introspected at runtime) ───────
# config/config_manager.py.get_bool — called via getattr(cfg, type_)
# in some dynamic-typed config loaders.
get_bool  # noqa: F821

# ── CacheManager public surface ──────────────────────────────────
# These methods exist on the manager API for diagnostics and tests
# even if no production call site references them statically.
clear_all  # noqa: F821
cache_size  # noqa: F821
registered_names  # noqa: F821

# ── CDP client public methods ────────────────────────────────────
# Used by auth flows and the edge browser orchestrator via
# dependency injection; no static call site in the package.
navigate  # noqa: F821
wait_for_url  # noqa: F821
connect_to_steam  # noqa: F821
is_steam_ui_tab  # noqa: F821
prepare_auth_launch  # noqa: F821
close_auth_browser  # noqa: F821
is_running  # noqa: F821

# ── Internal CDP/page helpers wrapped by decorators ──────────────
# @asynccontextmanager wrapped — vulture doesn't follow the wrap.
_session_timeout  # noqa: F821

# ── Compat / Proton wrappers (legacy passthroughs) ───────────────
# Called by the frontend via RPC; no static call in the codebase.
list_known_tools  # noqa: F821
check_version  # noqa: F821
fetch  # noqa: F821
get_for_app  # noqa: F821
set_for_app  # noqa: F821
clear_for_app  # noqa: F821

# ── Dataclass / typed result containers ──────────────────────────
# Used by attribute access only — vulture sees no method call so
# flags them as unused classes. They're consumed by the persistence
# layer and the RPC return-types.
EventPayload  # noqa: F821
UnifiDBResult  # noqa: F821
PlaySessionResult  # noqa: F821
GameStatsResult  # noqa: F821
DailyTotal  # noqa: F821

# ── Dataclass field names (StoreInfo & friends) ──────────────────
# StoreInfo fields are read through attribute access by every store;
# vulture flags the field declaration as unused.
auth_method  # noqa: F821
icon_asset  # noqa: F821
uses_wine  # noqa: F821
supports_install  # noqa: F821
supports_cloud_saves  # noqa: F821
min_version  # noqa: F821

# ── Event enum values (passed via string lookup) ─────────────────
# The bus normalises topics to strings; some event names appear
# only in subscriber declarations and are looked up by value.
SECURITY_PERMISSIONS_REPAIRED  # noqa: F821
SECURITY_BRUTEFORCE_SUSPECTED  # noqa: F821
SECURITY_DEVICE_RESET_DETECTED  # noqa: F821

# ── Identity helpers (consumed by validators) ────────────────────
is_safe_game_id  # noqa: F821
assert_all_keys_resolve  # noqa: F821

# ── Background fetcher API (called by launcher orchestration) ────
_shortcuts_registry_path  # noqa: F821
_config_degraded  # noqa: F821
