from typing import Any, Callable, Dict, List


Subscriber = Callable[[Dict[str, Any]], None]

_subscribers: List[Subscriber] = []


def subscribe(subscriber: Subscriber) -> None:
    _subscribers.append(subscriber)


def unsubscribe(subscriber: Subscriber) -> None:
    if subscriber in _subscribers:
        _subscribers.remove(subscriber)


def publish(event: Dict[str, Any]) -> None:
    payload = dict(event)
    for subscriber in list(_subscribers):
        subscriber(payload)
