from typing import List, Protocol
from domain.events.schemas import BaseEvent
from domain.logging.logger import get_trace_id


class EventPublisher(Protocol):
    def publish(self, event: BaseEvent) -> None:
        ...


class InMemoryEventPublisher:
    """In-memory event bus for local dev, testing, and sync pipelines."""

    def __init__(self) -> None:
        self.events: List[BaseEvent] = []

    def publish(self, event: BaseEvent) -> None:
        if not event.trace_id:
            event.trace_id = get_trace_id()
        self.events.append(event)

    def get_events(self) -> List[BaseEvent]:
        return list(self.events)

    def clear(self) -> None:
        self.events.clear()


_publisher = InMemoryEventPublisher()


def get_publisher() -> InMemoryEventPublisher:
    return _publisher


def publish_event(event: BaseEvent) -> None:
    _publisher.publish(event)


def get_published_events() -> List[BaseEvent]:
    return _publisher.get_events()


def clear_published_events() -> None:
    _publisher.clear()
