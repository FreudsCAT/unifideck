"""event_bus/bus_pipeline.py — Typed bundle of bus-pipeline components.

The EventBus pipeline is composed of five independent components
that are constructed once at plugin boot and live for the whole
process lifetime:

  - watchdog   : HandlerWatchdog — quarantines slow/raising handlers
  - latency    : HandlerLatencyCollector — per-handler latency stats
  - replay     : EventReplayBuffer — bounded ring buffer for late
                   subscribers (frontend reconnects, services that
                   register after some events have already fired)
  - batcher    : BatchDispatcher — coalesces high-frequency events
                   into single delivery passes
  - dispatcher : PriorityDispatcher — the actual event router that
                   composes the above four into a coherent pipeline

These are NOT services in the dependency-injection sense — they
are infrastructure that the services consume, not peers in the
service container. Keeping them in their own typed bundle (rather
than spreading them as flat attributes on Plugin) makes it
possible to pass the whole pipeline as a single argument to
``bootstrap_services`` so any service that needs a reference to
one of them (e.g. ProbeReactionService → watchdog) can pick it
up declaratively from a ``_SERVICE_DEFS`` lambda.

## Why a namedtuple instead of a dataclass

NamedTuple gives us:
  - Immutability — pipeline is built once, never mutated
  - Tuple-shaped destructuring for tests:
        ``watchdog, latency, replay, batcher, dispatcher = pipeline``
  - Zero overhead vs a plain tuple
  - Clear field names for IDE introspection

A dataclass would also work but adds mutability we don't want
and a ``__init__`` we don't need (NamedTuple's positional
constructor is enough for our use case).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .event_bus_scaling import BatchDispatcher
    from .event_replay import EventReplayBuffer
    from .priority_dispatcher import PriorityDispatcher
    from .supervision.metrics_handler import HandlerLatencyCollector
    from .supervision.watchdog_handler import HandlerWatchdog


class BusPipeline(NamedTuple):
    """Immutable bundle of the five EventBus pipeline components.

    Built once by ``Plugin._build_eventbus_pipeline`` and passed
    by value to ``bootstrap_services`` so service constructors
    can depend on specific pipeline components (currently only
    ``ProbeReactionService`` consumes ``watchdog``, but other
    services may grow such dependencies — e.g. a future debugging
    service might want to attach to ``latency`` or ``replay``).

    Fields are quoted forward-references resolved via
    ``TYPE_CHECKING`` so static checkers see the precise
    component types without triggering circular imports at runtime.
    """

    watchdog: HandlerWatchdog
    latency: HandlerLatencyCollector
    replay: EventReplayBuffer
    batcher: BatchDispatcher
    dispatcher: PriorityDispatcher
