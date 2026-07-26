"""WebRTC transport configuration shared by notify and media sessions."""

import logging
from collections.abc import Callable

from aioice import ice

logger = logging.getLogger(__name__)
_original_get_host_addresses: Callable[[bool, bool], list[str]] = ice.get_host_addresses


def configure_ice_candidates(*, ipv6_enabled: bool) -> None:
    """Publish only IPv4 host candidates unless IPv6 was explicitly enabled."""
    if ipv6_enabled:
        ice.get_host_addresses = _original_get_host_addresses
        return

    def get_ipv4_host_addresses(use_ipv4: bool, use_ipv6: bool) -> list[str]:
        return _original_get_host_addresses(use_ipv4=use_ipv4, use_ipv6=False)

    ice.get_host_addresses = get_ipv4_host_addresses
    logger.info("webrtc_ipv6_candidates_disabled")
