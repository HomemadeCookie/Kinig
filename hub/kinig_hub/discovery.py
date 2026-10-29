"""Announce the hub on the LAN with mDNS so pods find it without an IP.

The ESP32 asks for _kinig._tcp and gets this laptop's address and port.
Moving the hub to another laptop then needs no firmware change. If mDNS is
blocked (some Windows firewalls, some campus Wi-Fi), set KINIG_HUB_HOST in
firmware/kinig_pod/config.h to the IP the hub prints at startup.
"""

from __future__ import annotations

import logging
import socket

log = logging.getLogger("kinig.discovery")

SERVICE_TYPE = "_kinig._tcp.local."
SERVER_NAME = "kinig-hub.local."


def advertise(ip: str, port: int):
    """Register the service. Returns the Zeroconf object, or None if it failed."""
    if ip.startswith("127."):
        log.warning("No LAN address, so mDNS is off. Pods need KINIG_HUB_HOST set.")
        return None
    try:
        from zeroconf import ServiceInfo, Zeroconf

        info = ServiceInfo(
            SERVICE_TYPE,
            f"Kinig hub.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=port,
            server=SERVER_NAME,
        )
        zc = Zeroconf()
        zc.register_service(info, allow_name_change=True)
    except Exception as exc:  # mDNS is a convenience. The hub runs without it.
        log.warning("mDNS announce failed (%s). Pods need KINIG_HUB_HOST set.", exc)
        return None
    log.info("Pods can find this hub by mDNS as %s (%s:%s)", SERVER_NAME.rstrip("."), ip, port)
    return zc


def stop(zc) -> None:
    if zc is None:
        return
    try:
        zc.unregister_all_services()
        zc.close()
    except Exception:
        pass
