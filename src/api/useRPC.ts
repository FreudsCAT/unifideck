/**
 * Generic typed RPC hooks.
 *
 * `useRPCQuery` — fire-and-cache, auto-runs on mount, exposes
 *   {data, error, loading, refetch}. Use for read endpoints
 *   (get_store_infos, get_storage_locations, ...).
 *
 * `useRPCMutation` — manual trigger, exposes {mutate, error,
 *   loading}. Use for write endpoints (install_game, sync, ...).
 *
 * `useRPC` — low-level escape hatch returning a callable
 *   bound to a route. Most callers should prefer the two
 *   higher-level hooks above.
 *
 * All three honour the same error mapping: rejections are
 * wrapped in `RpcError` with structured fields so consumers
 * can switch on `.code` instead of regex-matching strings.
 */
import { call } from "@decky/api";
import { useCallback, useEffect, useRef, useState } from "react";
import { mapRpcError, RpcError } from "./rpc-errors";
import type { RouteName } from "./rpc-routes";

/**
 * Backend RPC envelope shape. Every method wrapped by
 * `@auto_wrap_rpc_methods` returns this three-key dict.
 * We unwrap it here so consumers see only `TReturn`.
 */
interface RpcEnvelope<T = unknown> {
  success: boolean;
  error: string | null;
  data: T;
}

/** Type guard for the backend envelope. */
function isRpcEnvelope(v: unknown): v is RpcEnvelope {
  return typeof v === "object" && v !== null && "success" in v && "data" in v;
}

/** Unwrap the backend ``{success, error, data}`` envelope.
 *
 *  ``useRPC`` does this for component callers automatically, but
 *  modules outside React (`AuthDispatcher`, `EventBusClient`,
 *  shortcut launchers) call ``call()`` directly and must unwrap
 *  manually. Exported here so the unwrap logic lives in one
 *  place — a change to the envelope shape is a one-file fix.
 *
 *  On a non-success envelope this throws `RpcError`, mirroring
 *  the hook behaviour. Pass `{ throwing: false }` to opt out
 *  (e.g. when the caller wants to inspect the failure shape
 *  itself).
 */
export function unwrapRpcEnvelope<T>(
  raw: unknown,
  options: { route?: string; throwing?: boolean } = {},
): T {
  const { route = "<unknown>", throwing = true } = options;
  if (isRpcEnvelope(raw)) {
    if (!raw.success && throwing) {
      throw new RpcError("UNKNOWN", raw.error ?? "RPC call failed", route, raw);
    }
    return raw.data as T;
  }
  return raw as T;
}

/** Low-level RPC caller. Returns a stable async function
 *  bound to the given route. Automatically unwraps the
 *  backend `{success, error, data}` envelope so callers
 *  receive only the `data` payload. */
export function useRPC<TArgs extends readonly unknown[], TReturn>(
  route: RouteName,
): (...args: TArgs) => Promise<TReturn> {
  return useCallback(
    async (...args: TArgs): Promise<TReturn> => {
      try {
        // @decky/api's call signature is variadic-typed; we
        // erase the variadic at the boundary and re-impose
        // the strict type via the generic.
        const raw = await call<unknown[], RpcEnvelope<TReturn> | TReturn>(
          route,
          ...(args as unknown as unknown[]),
        );

        // Unwrap the backend envelope if present.
        if (isRpcEnvelope(raw)) {
          if (!raw.success) {
            throw new RpcError(
              "UNKNOWN",
              raw.error ?? "RPC call failed",
              route,
              raw,
            );
          }
          return raw.data as TReturn;
        }

        // Fallback: if the response is not enveloped (should
        // not happen with the current backend, but defensive).
        return raw as TReturn;
      } catch (err) {
        if (err instanceof RpcError) throw err;
        throw mapRpcError(err, route);
      }
    },
    [route],
  );
}

/**
 * State machine returned by `useRPCQuery`. `loading` is
 * true on first fetch only ; subsequent `refetch` calls
 * keep `data` stable to avoid UI flicker.
 */
export interface QueryState<T> {
  data: T | null;
  error: RpcError | null;
  loading: boolean;
  refetch: () => Promise<void>;
}

/** Auto-fetch on mount + expose refetch. Caller can also
 *  pass `enabled=false` to defer until ready. */
export function useRPCQuery<TArgs extends readonly unknown[], TReturn>(
  route: RouteName,
  args: TArgs,
  options: { enabled?: boolean } = {},
): QueryState<TReturn> {
  const enabled = options.enabled !== false;
  const [data, setData] = useState<TReturn | null>(null);
  const [error, setError] = useState<RpcError | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const cancelledRef = useRef(false);
  const rpc = useRPC<TArgs, TReturn>(route);
  // Args identity changes per render; stringify for stable dep.
  const argsKey = JSON.stringify(args);
  /** Refetch. */
  const refetch = useCallback(async () => {
    if (cancelledRef.current) return;
    setLoading(true);
    setError(null);
    try {
      const result = await rpc(...args);
      if (!cancelledRef.current) setData(result);
    } catch (e) {
      if (!cancelledRef.current) setError(e as RpcError);
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, [rpc, argsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    cancelledRef.current = false;
    if (enabled) void refetch();
    return () => {
      cancelledRef.current = true;
    };
  }, [enabled, refetch]);
  return { data, error, loading, refetch };
}

/**
 * State machine returned by `useRPCMutation`. Unlike
 * a query, a mutation never auto-runs : the consumer
 * calls `mutate(args)` to trigger it. `data` and
 * `error` reflect the *last* invocation only.
 */
export interface MutationState<TArgs extends readonly unknown[], TReturn> {
  mutate: (...args: TArgs) => Promise<TReturn | null>;
  error: RpcError | null;
  loading: boolean;
  reset: () => void;
}

/** Manual-trigger RPC. The returned `mutate` resolves with
 *  the return value or null on error (error stored on state).
 *  Throwing variant available via `mutateOrThrow`. */
export function useRPCMutation<TArgs extends readonly unknown[], TReturn>(
  route: RouteName,
): MutationState<TArgs, TReturn> {
  const rpc = useRPC<TArgs, TReturn>(route);
  const [error, setError] = useState<RpcError | null>(null);
  const [loading, setLoading] = useState(false);
  const mutate = useCallback(
    async (...args: TArgs): Promise<TReturn | null> => {
      setLoading(true);
      setError(null);
      try {
        return await rpc(...args);
      } catch (e) {
        setError(e as RpcError);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [rpc],
  );
  /** Reset. */
  const reset = useCallback(() => {
    setError(null);
    setLoading(false);
  }, []);
  return { mutate, error, loading, reset };
}
