/**
 * RPC error mapping.
 *
 * Decky's `call()` throws plain Error objects on RPC failure.
 * Components benefit from a structured shape so they can
 * switch on `.code` (e.g. show a re-auth prompt on
 * AUTH_REQUIRED, a toast on TIMEOUT) without parsing the
 * message string. `mapRpcError` is the single point that
 * normalises raw rejections into `RpcError`.
 */
export type RpcErrorCode =
  | "TIMEOUT"
  | "AUTH_REQUIRED"
  | "STORE_UNAVAILABLE"
  | "BACKEND_NOT_READY"
  | "BAD_ARGUMENTS"
  | "UNKNOWN";

/**
 * Typed error raised when an RPC call fails. Carries the
 * route name and the typed backend error code so callers
 * can branch on the cause (auth-expired, network-timeout,
 * disk-full…) without parsing free-form messages.
 */
export class RpcError extends Error {
  readonly code: RpcErrorCode;
  readonly route: string;
  readonly cause: unknown;
  constructor(
    code: RpcErrorCode,
    message: string,
    route: string,
    cause: unknown,
  ) {
    super(message);
    this.name = "RpcError";
    this.code = code;
    this.route = route;
    this.cause = cause;
  }
}

/** Heuristic mapper from raw rejection → typed code. Backend
 *  exposes structured errors via `Result.error` strings; we
 *  match a small allowlist of well-known prefixes and fall
 *  through to UNKNOWN for the rest. */
export function mapRpcError(raw: unknown, route: string): RpcError {
  const message = extractMessage(raw);
  const code = inferCode(message);
  return new RpcError(code, message, route, raw);
}

/** Extract message. */
function extractMessage(raw: unknown): string {
  if (raw instanceof Error) return raw.message;
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object" && "message" in raw) {
    return String((raw as { message: unknown }).message);
  }
  return "RPC call failed";
}

/** Infer code. */
function inferCode(message: string): RpcErrorCode {
  const m = message.toLowerCase();
  if (m.includes("timeout") || m.includes("timed out")) return "TIMEOUT";
  if (m.includes("auth") && m.includes("required")) return "AUTH_REQUIRED";
  if (m.includes("not authenticated")) return "AUTH_REQUIRED";
  if (m.includes("store unavailable")) return "STORE_UNAVAILABLE";
  if (m.includes("not ready")) return "BACKEND_NOT_READY";
  if (m.includes("bad arguments") || m.includes("invalid")) {
    return "BAD_ARGUMENTS";
  }
  return "UNKNOWN";
}
