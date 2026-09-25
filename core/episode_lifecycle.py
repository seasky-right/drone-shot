"""Shared episode lifecycle mechanics without task or vehicle policy."""
from __future__ import annotations

import time
from enum import Enum
from threading import Event
from typing import Callable, Iterable


class LifecycleState(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    STOPPED = "stopped"


class EpisodeLifecycle:
    """Ordered events, cancellation checks, and resource/close bookkeeping.

    Events are allowed before begin so factories can record load events. Callers
    decide termination policy and emit its final event after cleanup.
    """

    def __init__(self, event_sink: Callable[[dict[str, object]], None], *,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock_ns: Callable[[], int] = time.time_ns,
                 on_event: Callable[[dict[str, object]], None] | None = None,
                 cancel_event: Event | None = None) -> None:
        self.event_sink = event_sink
        self.clock = clock
        self.wall_clock_ns = wall_clock_ns
        self.cancel_event = cancel_event if cancel_event is not None else Event()
        self._owns_cancel_event = cancel_event is None
        self.state = LifecycleState.NEW
        self.sequence = 0
        self.resource = None
        self.resource_acquired = False
        self._subscribers: list[Callable[[dict[str, object]], None]] = []
        if on_event is not None:
            self.subscribe(on_event)

    def subscribe(self, callback: Callable[[dict[str, object]], None]) -> None:
        self._subscribers.append(callback)

    def begin(self) -> None:
        if self.state is not LifecycleState.NEW:
            raise RuntimeError("episode lifecycle has already begun")
        if self._owns_cancel_event:
            self.cancel_event.clear()
        self.state = LifecycleState.ACTIVE

    def end(self) -> None:
        if self.state is LifecycleState.STOPPED:
            return
        self.state = LifecycleState.STOPPED

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def checkpoint(self, elapsed_s: float, budget_s: float, *, steps: int = 0,
                   max_steps: int | None = None) -> str | None:
        if self.cancel_event.is_set():
            return "cancelled"
        if elapsed_s >= budget_s or (max_steps is not None and steps >= max_steps):
            return "timeout"
        return None

    def emit(self, source: str, kind: str, **fields: object) -> dict[str, object]:
        event = {"sequence": self.sequence, "source": source, "kind": kind,
                 "wall_time_ns": self.wall_clock_ns(), "fields": fields}
        self.sequence += 1
        self.event_sink(event)
        for subscriber in tuple(self._subscribers):
            try:
                subscriber(event)
            except Exception:
                pass
        return event

    def acquire(self, runtime, episode_id: str, config: object) -> object | None:
        if runtime is None:
            return None
        self.resource = runtime.acquire(episode_id, config)
        self.resource_acquired = True
        self.emit("runner", "runtime_acquired")
        return self.resource

    def _cleanup_event(self, source: str, kind: str, **fields: object) -> None:
        try:
            self.emit(source, kind, **fields)
        except Exception:
            pass

    def close(self, components: Iterable[tuple[str, object]], *, runtime=None,
              error: Callable[[Exception], object] | None = None) -> tuple[str, ...]:
        failures: list[str] = []
        for stage, component in components:
            try:
                component.close()
            except (Exception, KeyboardInterrupt) as exc:
                failures.append(f"{stage}: {str(exc) or type(exc).__name__}")
                self._cleanup_event("runner", "close_error", stage=stage,
                                    error=error(exc) if error is not None else str(exc))
        if runtime is not None:
            if self.resource_acquired:
                try:
                    runtime.release(self.resource)
                    self._cleanup_event("runner", "runtime_released")
                except (Exception, KeyboardInterrupt) as exc:
                    failures.append(f"runtime.release: {str(exc) or type(exc).__name__}")
                    self._cleanup_event("runner", "runtime_release_error",
                                        error=error(exc) if error is not None else str(exc))
                finally:
                    self.resource_acquired = False
            close_runtime = getattr(runtime, "close", None)
            if close_runtime is not None:
                try:
                    close_runtime()
                except (Exception, KeyboardInterrupt) as exc:
                    failures.append(f"runtime.close: {str(exc) or type(exc).__name__}")
                    self._cleanup_event("runner", "runtime_close_error",
                                        error=error(exc) if error is not None else str(exc))
        return tuple(failures)
