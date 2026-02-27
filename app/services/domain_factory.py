"""Factory for resolving Domain instances by name."""

import logging
import os

from app.services.domain import Domain
from app.services.domains.fashion import FashionDomain

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type] = {
    "fashion": FashionDomain,
}


def get_domain(name: str | None = None) -> Domain:
    """
    Return a Domain instance by name.

    Reads the DOMAIN environment variable if name is None; defaults to 'fashion'.

    Args:
        name: Domain name string, or None to read from the DOMAIN env var.

    Returns:
        A fresh instance of the requested Domain implementation.

    Raises:
        ValueError: If the domain name is not registered.
    """
    domain_name = name or os.environ.get("DOMAIN", "fashion")
    cls = _REGISTRY.get(domain_name)
    if cls is None:
        raise ValueError(
            f"Unknown domain '{domain_name}'. Available: {list(_REGISTRY.keys())}"
        )
    logger.debug("Domain resolved: %s -> %s", domain_name, cls.__name__)
    return cls()
