import logging
from typing import Any, Callable, Dict, List


logger = logging.getLogger(__name__)

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
        try:
            subscriber(dict(payload))
        except Exception:
            logger.exception("Realtime subscriber failed")
