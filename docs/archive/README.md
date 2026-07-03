# Archived Documentation

This folder holds documentation that is **no longer a living reference** but is kept
for historical context. Two kinds of files live here:

1. **Superseded reference docs** — documents whose content has been overtaken by code
   changes and replaced by a current doc in `docs/`.
2. **Point-in-time records** — dated session logs and bug-fix postmortems. These
   accurately describe work that was completed and shipped; they are snapshots, not
   docs that get kept up to date.

> Nothing here is guaranteed to match the current code. For current architecture and
> behaviour, use the docs in `docs/` (start with [`architecture.md`](../architecture.md)).

_Archived on 2026-06-22 during a documentation audit._

## Contents

### Superseded reference docs

| File | Why archived | Current replacement |
| ---- | ------------ | ------------------- |
| `ARCHITECTURE_TREE.md` | A volunteer "contribution map" with `OP-XX` ticket IDs / `NotImplementedError` stubs for the 0.7 refactor, which is complete (no `OP-XX` markers remain in code). Paths were stale (`core/bin/` → `core/binaries/`, `service/` → `services/`). | [`architecture.md`](../architecture.md) |
| `sync-gap-analysis.md` | 2026-05-18 gap analysis of the sync pipeline. The large majority of the P0/P1 gaps it lists (artwork re-sync, cache snapshot, request queueing, progress-payload counters, post-complete hang) are now implemented. | Behaviour is current in `services/sync_service.py`, `core/sync_progress.py`, `services/artwork/` |
| `ubisoft-implementation-tracker.md` | Phase tracker referencing pre-refactor file paths (`stores/ubisoft_api.py`, `stores/ubisoft.py`) that no longer exist; the integration is now the `stores/ubisoft/` package. | [`ubisoft-store-spec.md`](../ubisoft-store-spec.md) (v2) |
| `ubisoft-store-spec-v1.md` † | The March-2026 design spec. Auth (§4) and Library (§5) no longer match the code — the live integration is shortcut-launch UPC auth + local UPC-binary library parsing (no REST/2FA, no GraphQL), and the class structure was refactored into specialist subpackages. | [`ubisoft-store-spec.md`](../ubisoft-store-spec.md) (v2) |

### Point-in-time records (historical, accurate as snapshots)

| File | What it records |
| ---- | --------------- |
| `post-merge-bugfixes.md` | Postmortem of ~20 bugs fixed after a merge (SteamGridDB stalls, SSL fallback, auth-window visibility in Gaming Mode, etc.). |
| `UBISOFT_INTEGRATION_FIXES.md` † | Postmortem of the first round of Ubisoft launch/auth/shortcut bugs and their fixes. |
| `Documented Changes/` | Dated session change logs from the F1–F8 frontend refactor recovery and the 0.7 sync/artwork work. Each entry pairs a symptom with its root cause, fix, and verification — useful "how we got here" reading. |

> † Retained locally only — intentionally kept out of version control (see `.gitignore`),
> so these files are **not** present in a fresh clone.
