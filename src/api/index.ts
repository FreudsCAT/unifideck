/**
 * RPC client subpackage — barrel export.
 *
 * Single import surface for every RPC interaction. Components
 * use `useRPC` to call backend methods, never the raw `call()`
 * from `@decky/api`. The wrapper adds typing, error mapping
 * and a unified retry policy.
 */
export { useRPC, useRPCQuery, useRPCMutation } from "./useRPC";
export { rpcRoutes, isKnownRoute, type RouteName } from "./rpc-routes";
export { mapRpcError, RpcError } from "./rpc-errors";
export { EventBusClient, useEventBus } from "./event-bus-client";
