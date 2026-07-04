# Unifideck — New Architecture Directory Tree

> **🗄️ ARCHIVED (2026-06-22) — outdated.** This was a contribution map for the 0.7
> refactor (now complete; no `OP-XX`/`NotImplementedError` stubs remain). Some paths are
> stale (`core/bin/` → `core/binaries/`, `service/` → `services/`). For current
> architecture see [`../architecture.md`](../architecture.md).

> **Branch:** `new-architecture` | **Doc version:** v1.2 cherry pik — April 2026
> **Reference:** `unifideck_plan_operationnel_integration_v1.2_cherry_pik_backend_only.pdf` > **Author:** HardCPP (src893) | **Validator:** mubaraknumann

This document is the volunteer contribution map. Every file has an **OP-XX** ticket ID,
a one-line description, its PDF page, and its dependency list. Pick a file, implement it
using the PDF spec, and open a PR targeting `new-architecture`.

---

## How to contribute

1. **Pick a file** — choose an `OP-XX` entry below (preferably one whose dependencies are already implemented)
2. **Read the PDF spec** — full implementation details are on the page listed
3. **Implement** — replace `raise NotImplementedError("OP-XX...")` with real code
4. **Test** — `pytest tests/` must pass (or add tests)
5. **PR** — open PR against `new-architecture`, reference the OP-XX in your PR title

**Dependency order:** implement Layer 1 (core/types) before Layer 2 (core services) before Layer 3 (StoreBase) before Layer 4 (stores) before Layer 5 (services).

---

## Layer 1 — Core Types (zero dependencies)

| OP     | File                                         | Description                   | PDF page | Status  |
| ------ | -------------------------------------------- | ----------------------------- | -------- | ------- |
| OP-05a | `py_modules/unifideck/core/types/events.py`  | Events + StoreStatus + enums  | p.23     | ✅ Full |
| OP-05b | `py_modules/unifideck/core/types/results.py` | Result hierarchy + StoreError | p.25     | ✅ Full |
| OP-05c | `py_modules/unifideck/core/types/domain.py`  | Game, StoreInfo, CLITool      | p.28     | ✅ Full |

---

## Layer 2 — Core Services

### core/io/ — Async filesystem I/O

| OP     | File                        | Description                               | PDF page | Depends |
| ------ | --------------------------- | ----------------------------------------- | -------- | ------- |
| OP-06a | `core/io/async_file_ops.py` | Non-blocking file ops (asyncio.to_thread) | p.30     | —       |
| OP-06b | `core/io/safe_file_op.py`   | OSError decorator factory                 | p.32     | —       |

### core/bin/ — CLI tool resolution

| OP     | File                            | Description                       | PDF page | Depends |
| ------ | ------------------------------- | --------------------------------- | -------- | ------- |
| OP-07a | `core/bin/binary_resolver.py`   | 3-tier binary locator             | p.34     | OP-05   |
| OP-07b | `core/bin/binary_signatures.py` | SHA256 allowlist for bundled CLIs | p.35     | —       |
| OP-07c | `core/bin/cli_timeouts.py`      | Shared CLI timeout config         | p.36     | OP-11a  |

### core/net/ — Network helpers

| OP     | File                      | Description                      | PDF page | Depends |
| ------ | ------------------------- | -------------------------------- | -------- | ------- |
| OP-08a | `core/net/ssl_helpers.py` | Centralised SSL context builders | p.38     | —       |

### core/ — Core singletons

| OP     | File                        | Description                                | PDF page | Depends        |
| ------ | --------------------------- | ------------------------------------------ | -------- | -------------- |
| OP-04a | `core/cache_manager.py`     | CacheStore + CacheManager (9 pairs → 1)    | p.39     | OP-05          |
| OP-04b | `core/metrics_collector.py` | Per-event metrics aggregator               | p.41     | OP-09a         |
| OP-04c | `core/exe_finder.py`        | Game executable locator (score-based)      | p.42     | OP-05          |
| OP-04d | `core/sync_service.py`      | Library sync orchestrator (623L → generic) | p.43     | OP-47a, OP-47b |
| OP-04e | `core/manifest.py`          | Per-game manifest + discovery scan         | p.45     | OP-09a, OP-33a |
| OP-04f | `core/paths.py`             | Plugin root resolution                     | p.47     | —              |

---

## Layer 2 — EventBus

| OP     | File                                        | Description                                 | PDF page | Depends        |
| ------ | ------------------------------------------- | ------------------------------------------- | -------- | -------------- |
| OP-09a | `event_bus/event_bus.py`                    | Pub/sub with async gather + error isolation | p.53     | OP-05, OP-09b  |
| OP-09b | `event_bus/event_priority.py`               | CRITICAL/NORMAL/BACKGROUND classification   | p.55     | OP-05          |
| OP-09c | `event_bus/priority_dispatcher.py`          | PriorityQueue + coalescing + backpressure   | p.57     | OP-09b         |
| OP-09d | `event_bus/event_replay.py`                 | Ring buffer of recent events                | p.60     | OP-05          |
| OP-09e | `event_bus/event_bus_extensions.py`         | DeadLetterQueue, PredicateFilter, Schema    | p.62     | OP-05          |
| OP-09f | `event_bus/event_bus_reliability.py`        | Circuit breaker per handler                 | p.64     | —              |
| OP-09g | `event_bus/event_bus_scaling.py`            | Batch dispatcher                            | p.65     | —              |
| OP-09h | `event_bus/event_bus_devex.py`              | @subscribe decorator + auto_wire            | p.66     | —              |
| OP-09i | `event_bus/bus_pipeline.py`                 | Assembled bus pipeline                      | p.68     | OP-09a, OP-09c |
| OP-10a | `event_bus/supervision/watchdog_handler.py` | Timeout + quarantine                        | p.50     | —              |
| OP-10b | `event_bus/supervision/metrics_handler.py`  | p50/p95 latency rolling window              | p.52     | —              |

---

## Layer 2 — Config

| OP     | File                           | Description                        | PDF page | Depends        |
| ------ | ------------------------------ | ---------------------------------- | -------- | -------------- |
| OP-11a | `config/config_manager.py`     | Centralized config with hot-reload | p.70     | OP-05          |
| OP-11b | `config/config_persistence.py` | Atomic load/save                   | p.72     | —              |
| OP-11c | `config/i18n_schema.py`        | Locale list schema                 | p.73     | —              |
| OP-11d | `config/schema.json`           | JSON Schema for config validation  | p.74     | —              |
| OP-11e | `config/validator.py`          | jsonschema validation              | p.84     | —              |
| OP-11f | `config/key_presence.py`       | Required key presence check        | p.87     | —              |
| OP-11g | `config/startup.py`            | Startup config validation          | p.89     | OP-11a, OP-11e |
| OP-11h | `config/user_config_path.py`   | User config path resolution        | p.90     | —              |

---

## Layer 3 — StoreBase ABC + StoreRegistry

| OP     | File                                   | Description                            | PDF page | Depends |
| ------ | -------------------------------------- | -------------------------------------- | -------- | ------- |
| OP-47a | `stores/shared/store_registry.py`      | Dict dispatcher (replaces 109 if/elif) | p.387    | —       |
| OP-47b | `stores/shared/store_base.py`          | 6-abstract-method ABC + 4 utilities    | p.390    | OP-05   |
| OP-47c | `stores/shared/dlc.py`                 | DLC detection utilities                | p.394    | OP-05   |
| OP-47d | `stores/shared/cli_install_helpers.py` | CLI install helper functions           | p.395    | OP-07a  |

### RPC layer (needed before stores can be wired to Plugin)

| OP     | File               | Description                      | PDF page | Depends |
| ------ | ------------------ | -------------------------------- | -------- | ------- |
| OP-24a | `rpc/errors.py`    | RPC error types                  | p.223    | —       |
| OP-24b | `rpc/wrapper.py`   | RPC method error wrapper         | p.224    | OP-24a  |
| OP-24c | `rpc/auto_wire.py` | @auto_wrap_rpc_methods decorator | p.225    | OP-24b  |
| OP-24d | `rpc/composer.py`  | Mixin composer                   | p.226    | OP-24c  |

---

## Layer 4 — Store Plugins (106 files, 13,173 lines)

### EpicStore (legendary CLI)

| OP     | File                          | PDF page |
| ------ | ----------------------------- | -------- |
| OP-48a | `stores/epic/store.py`        | p.398    |
| OP-48b | `stores/epic/auth.py`         | p.401    |
| OP-48c | `stores/epic/library.py`      | p.403    |
| OP-48d | `stores/epic/install.py`      | p.405    |
| OP-48e | `stores/epic/updates.py`      | p.408    |
| OP-48f | `stores/epic/filter.py`       | p.410    |
| OP-48g | `stores/epic/exe_resolver.py` | p.411    |
| OP-48h | `stores/epic/legendary.py`    | p.413    |

### AmazonStore (nile CLI)

| OP     | File                              | PDF page |
| ------ | --------------------------------- | -------- |
| OP-49a | `stores/amazon/amazon_store.py`   | p.415    |
| OP-49b | `stores/amazon/amazon_auth.py`    | p.418    |
| OP-49c | `stores/amazon/amazon_library.py` | p.420    |
| OP-49d | `stores/amazon/amazon_install.py` | p.422    |
| OP-49e | `stores/amazon/amazon_updates.py` | p.425    |
| OP-49f | `stores/amazon/amazon_fuel.py`    | p.427    |

### GOGStore (gogdl CLI)

| OP       | File                              | PDF page  |
| -------- | --------------------------------- | --------- |
| OP-50a   | `stores/gog/store.py`             | p.460     |
| OP-50b   | `stores/gog/config.py`            | p.463     |
| OP-50c   | `stores/gog/library.py`           | p.465     |
| OP-50d   | `stores/gog/library_migration.py` | p.467     |
| OP-50e   | `stores/gog/exe_resolver.py`      | p.469     |
| OP-50f   | `stores/gog/dlc.py`               | p.472     |
| OP-50g   | `stores/gog/updates.py`           | p.475     |
| OP-50h   | `stores/gog/auth.py`              | p.478     |
| OP-50i   | `stores/gog/http.py`              | p.480     |
| OP-51a–h | `gog/install/*.py`                | p.430–448 |

### MicrosoftStore (xCloud streaming)

| OP       | File                                         | PDF page  |
| -------- | -------------------------------------------- | --------- |
| OP-53a   | `stores/microsoft/microsoft_store.py`        | p.490     |
| OP-53b   | `stores/microsoft/microsoft_catalog.py`      | p.493     |
| OP-53c   | `stores/microsoft/microsoft_browser_auth.py` | p.495     |
| OP-53d   | `stores/microsoft/microsoft_config.py`       | p.497     |
| OP-53e   | `stores/microsoft/microsoft_auth.py`         | p.500     |
| OP-53f   | `stores/microsoft/microsoft_subscription.py` | p.502     |
| OP-54a–d | `stores/microsoft/tokens/*.py`               | p.483–488 |

### UbisoftStore (Wine + UPC — most complex, 4,640L → ~20 focused)

| OP       | File                            | PDF page  |
| -------- | ------------------------------- | --------- |
| OP-55a   | `stores/ubisoft/store.py`       | p.586     |
| OP-55b–j | `stores/ubisoft/*.py`           | p.590–613 |
| OP-56a–h | `stores/ubisoft/installer/*.py` | p.507–525 |
| OP-57a–i | `stores/ubisoft/library/*.py`   | p.528–550 |
| OP-58a–f | `stores/ubisoft/auth/*.py`      | p.552–564 |
| OP-59a–d | `stores/ubisoft/prefix/*.py`    | p.567–574 |
| OP-60a–d | `stores/ubisoft/session/*.py`   | p.577–584 |

### GOG tokens

| OP       | File                 | PDF page  |
| -------- | -------------------- | --------- |
| OP-52a–e | `stores/tokens/*.py` | p.451–459 |

---

## Layer 5 — Infrastructure Services (75 files, 4,168 lines)

| OP       | Module                            | Description                              | PDF page  |
| -------- | --------------------------------- | ---------------------------------------- | --------- |
| OP-14a–g | `service/shortcut/`               | ShortcutService — VDF + games.map        | p.103–111 |
| OP-15a–e | `service/download/`               | DownloadService — queue + dispatch       | p.113–118 |
| OP-16a–c | `service/artwork/`                | ArtworkService — SteamGridDB             | p.120–122 |
| OP-17a–f | `service/cloud_save/`             | CloudSaveService                         | p.124–130 |
| OP-18a–b | `service/playtime/`               | PlaytimeService + SQLite                 | p.132–134 |
| OP-19a–j | `service/security/`               | SecurityService + audit                  | p.137–147 |
| OP-20a–f | `service/launcher/`               | LauncherService + circuit breaker        | p.149–156 |
| OP-21a–f | `service/launch_history/`         | LaunchHistoryService                     | p.158–164 |
| OP-22a–g | `service/microsoft_subscription/` | Xbox subscription service                | p.166–173 |
| OP-12a–e | `service/*.py`                    | Metadata, Proton, Account, Feature flags | p.174–181 |

---

## Layer 5 — Support Modules (155 files, 15,564 lines)

| OP       | Module           | Description                               | PDF page  |
| -------- | ---------------- | ----------------------------------------- | --------- |
| OP-23a–e | `security/`      | Device identity, encrypted tokens, audit  | p.184–194 |
| OP-25a–h | `rpc/handlers/`  | Typed RPC handler classes                 | p.198–206 |
| OP-26a–k | `rpc/mixins/`    | RPC mixins for Plugin class               | p.209–222 |
| OP-27a–b | `actions/`       | unifideck:// URI handler                  | p.228–231 |
| OP-28–29 | `auth/`          | Browser + Edge OAuth                      | p.233–246 |
| OP-30a–d | `cdp/`           | CDP client + injection                    | p.253–264 |
| OP-31a–b | `metadata/`      | Metacritic + UnifiDB fetchers             | p.267–271 |
| OP-32a–c | `steam/`         | SteamGridDB + library + shortcuts         | p.275–280 |
| OP-33a–c | `utils/`         | Paths, locale, config helpers             | p.283–288 |
| OP-34a–b | `compatibility/` | Proton + library compat                   | p.291–296 |
| OP-35–45 | `launcher/`      | Launch flows, CDP, Proton, language setup | p.300–376 |

---

## Bootstrap

| OP     | File                            | Description                         | PDF page | Depends |
| ------ | ------------------------------- | ----------------------------------- | -------- | ------- |
| OP-61a | `bootstrap/cache_registry.py`   | Register all CacheManager caches    | p.616    | OP-04a  |
| OP-61b | `bootstrap/teardown.py`         | Plugin shutdown sequence            | p.617    | OP-13f  |
| OP-61c | `bootstrap/pipeline_factory.py` | Build EventBus + PriorityDispatcher | p.618    | OP-09i  |
| OP-61d | `bootstrap/boot.py`             | Full plugin boot sequence           | p.619    | OP-13e  |

---

## Root files (already implemented)

| File                        | Description                                             | Status |
| --------------------------- | ------------------------------------------------------- | ------ |
| `main.py`                   | Thin Plugin router — 11 RPC mixins, delegates all logic | ✅     |
| `requirements.txt`          | Runtime dependencies                                    | ✅     |
| `requirements-dev.txt`      | Dev dependencies (pytest, mypy, ruff)                   | ✅     |
| `defaults/config.json`      | Complete 374-line config with all store settings        | ✅     |
| `bin/unifideck-launcher.py` | Python launcher shim                                    | ✅     |

---

## File counts

| Category                          | Files   | Lines      |
| --------------------------------- | ------- | ---------- |
| Core (OP-01–11)                   | 21      | 1,670      |
| Store connectors (OP-46–60)       | 106     | 13,173     |
| Services (OP-12–22)               | 75      | 4,168      |
| Support refactored (OP-23–45, 61) | 155     | 15,564     |
| **Total backend**                 | **357** | **34,575** |
