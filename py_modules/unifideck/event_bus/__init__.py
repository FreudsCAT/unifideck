"""unifideck.event_bus — Event bus subpackage.

Centralises all modules related to the pub/sub event bus that
powers Unifideck's inter-service communication. Extracted from
`unifideck.core` where these 10 modules had grown into their own
cohesive unit but still shared space with type definitions,
cache management, config, and store registration.

Contents
--------

Core dispatch:
  - event_bus : EventBus, the main pub/sub hub
  - event_priority : EventPriority enum + priority lookup
  - priority_dispatcher : PriorityDispatcher that consumes the bus

Reliability:
  - watchdog_handler : HandlerWatchdog with quarantine detection
  - metrics_handler : HandlerLatencyCollector + per-handler stats
  - event_bus_reliability : CircuitBreaker for repeat-failing handlers
  - event_bus_scaling : BatchDispatcher for same-type coalescing

Developer experience:
  - event_replay : EventReplayBuffer for event log replay
  - event_bus_extensions : DeadLetterQueue, PredicateFilter,
                             TypedEventRegistry, EventSchema, DebugSnapshot
  - event_bus_devex : subscribe decorator + auto_wire + SchemaExtractor

The subpackage has **zero external dependencies** beyond stdlib —
no aiohttp, no decky, no Steam-specific code. Every module here
can be unit-tested in complete isolation.

Convenience re-exports below let callers write
`from unifideck.event_bus import EventBus` instead of the longer
`from unifideck.event_bus.event_bus import EventBus`. Only the
most-used names are promoted; specialised symbols stay in their
submodules to avoid polluting the namespace.

Reference: refactor notes — architectural extraction
from core/ into a dedicated subpackage for clarity and to reflect
the real module cohesion.
"""
from .event_bus import EventBus  # noqa: F401
from .event_bus_devex import (  # noqa: F401
    SchemaExtractor,
    auto_wire,
    subscribe,
)
from .event_bus_extensions import (  # noqa: F401
    DeadLetterQueue,
    DebugSnapshot,
    EventSchema,
    PredicateFilter,
    TypedEventRegistry,
)
from .event_bus_reliability import CircuitBreaker  # noqa: F401
from .event_bus_scaling import BatchDispatcher  # noqa: F401
from .event_priority import (  # noqa: F401
    EventPriority,
    get_coalesce_key,
    get_priority,
)
from .event_replay import EventReplayBuffer  # noqa: F401
from .priority_dispatcher import PriorityDispatcher  # noqa: F401
from .supervision.metrics_handler import (  # noqa: F401
    HandlerLatencyCollector,
    HandlerLatencyStats,
)
from .supervision.watchdog_handler import (  # noqa: F401
    HandlerQuarantinedError,
    HandlerWatchdog,
)

__all__ = [
    "EventBus",
    "EventPriority",
    "get_priority",
    "get_coalesce_key",
    "PriorityDispatcher",
    "HandlerWatchdog",
    "HandlerQuarantinedError",
    "HandlerLatencyCollector",
    "HandlerLatencyStats",
    "EventReplayBuffer",
    "DeadLetterQueue",
    "PredicateFilter",
    "TypedEventRegistry",
    "EventSchema",
    "DebugSnapshot",
    "CircuitBreaker",
    "BatchDispatcher",
    "subscribe",
    "auto_wire",
    "SchemaExtractor",
]
