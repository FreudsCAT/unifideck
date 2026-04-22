# OP-09 | event_bus/__init__.py | Depends: (none)
from .event_bus import EventBus
from .event_bus_devex import (
    SchemaExtractor,
    auto_wire,
    subscribe,
)
from .event_bus_extensions import (
    DeadLetterQueue,
    DebugSnapshot,
    EventSchema,
    PredicateFilter,
    TypedEventRegistry,
)
from .event_bus_reliability import CircuitBreaker
from .event_bus_scaling import BatchDispatcher
from .event_priority import (
    EventPriority,
    get_coalesce_key,
    get_priority,
)
from .event_replay import EventReplayBuffer
from .priority_dispatcher import PriorityDispatcher
from .supervision.metrics_handler import (
    HandlerLatencyCollector,
    HandlerLatencyStats,
)
from .supervision.watchdog_handler import (
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
    "BatchDispatcher",
    "CircuitBreaker",
    "subscribe",
    "auto_wire",
    "SchemaExtractor",
]
