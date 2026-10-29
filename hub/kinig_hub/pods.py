"""Pods the hub knows about. Kept in memory on purpose.

A pod says hello with its MAC, shows a 4-digit code on its screen, and waits.
The host sees the same code on /host and approves it. After that the host's
Start and Stop buttons drive the pod: the pod polls GET /pods/{id} and does
what the state says.

Restarting the hub forgets every pod, so each one pairs again. That is fine
for the prototype and shows the pairing step in every demo. There is no key
exchange. This is a demo of the flow, not security.
"""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass, field

# A pod that has not polled for this long is shown as offline on /host, and
# Stop closes its session on the hub instead of waiting for the pod.
ONLINE_WINDOW_S = 5.0

_POD_ID = re.compile(r"^[0-9a-z]{1,32}$")


def normalize_pod_id(raw: str) -> str | None:
    """AA:BB:CC:DD:EE:FF from the ESP32 becomes aabbccddeeff."""
    cleaned = re.sub(r"[^0-9a-zA-Z]", "", str(raw or "")).lower()
    return cleaned if _POD_ID.match(cleaned) else None


@dataclass
class Pod:
    pod_id: str
    code: str
    paired: bool = False
    session_id: str | None = None
    stop_requested: bool = False
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def label(self) -> str:
        return f"Pod {self.pod_id[-4:].upper()}"

    def online(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return current - self.last_seen <= ONLINE_WINDOW_S


class PodRegistry:
    def __init__(self, rng: random.Random | None = None):
        self._pods: dict[str, Pod] = {}
        self._lock = threading.Lock()
        self._rng = rng or random.Random()

    def hello(self, pod_id: str) -> tuple[Pod, str | None]:
        """Register a pod, or welcome back one that rebooted.

        Returns the pod and the id of a recording session the reboot cut off,
        so the caller can close it. A rebooted pod restarts seq at 0 and cannot
        continue the old session.
        """
        with self._lock:
            pod = self._pods.get(pod_id)
            if pod is None:
                pod = Pod(pod_id=pod_id, code=self._new_code())
                self._pods[pod_id] = pod
                return pod, None
            orphan = pod.session_id
            pod.session_id = None
            pod.stop_requested = False
            pod.last_seen = time.monotonic()
            return pod, orphan

    def get(self, pod_id: str) -> Pod | None:
        with self._lock:
            return self._pods.get(pod_id)

    def seen(self, pod_id: str) -> Pod | None:
        with self._lock:
            pod = self._pods.get(pod_id)
            if pod is not None:
                pod.last_seen = time.monotonic()
            return pod

    def all(self) -> list[Pod]:
        with self._lock:
            return list(self._pods.values())

    def forget(self, pod_id: str) -> Pod | None:
        with self._lock:
            return self._pods.pop(pod_id, None)

    def _new_code(self) -> str:
        taken = {pod.code for pod in self._pods.values()}
        while True:
            code = f"{self._rng.randrange(10_000):04d}"
            if code not in taken:
                return code
