/**
 * Services — barrel export.
 *
 * Layer-5 services contain non-UI business logic that lives
 * outside React components but inside the frontend. Each
 * service has its own subpackage : `auth/` for the auth
 * dispatcher pattern. Future ones could mediate cloud-save
 * UI flows, controller config orchestration, etc.
 *
 * Services are NEVER instantiated by components — they are
 * mounted by the plugin entry (Phase F6) and exposed via
 * React context or as singletons. Components reach them
 * through hooks in `hooks/` (Phase F3).
 */
export * from "./auth";
