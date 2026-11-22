"""Fail-closed capability gate for future untrusted browser children."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ContainmentUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainmentCapability:
    platform: str
    process_tree: bool
    resource_limits: bool
    network_isolation: bool
    filesystem_isolation: bool

    @property
    def browser_ready(self) -> bool:
        return self.process_tree and self.resource_limits and self.network_isolation and self.filesystem_isolation


def inspect_capability(platform: str = None) -> ContainmentCapability:
    """Report effective local controls, never configured intentions."""
    platform = os.name if platform is None else platform
    if platform == "nt":
        # Job Objects provide process/resource controls. They do not provide
        # default-deny network or filesystem isolation.
        return ContainmentCapability("windows", True, True, False, False)
    if platform == "posix":
        # Rootless container/cgroup setup is deployment-owned and absent here.
        return ContainmentCapability("linux", True, False, False, False)
    return ContainmentCapability(platform, False, False, False, False)


def require_browser_containment() -> ContainmentCapability:
    capability = inspect_capability()
    if not capability.browser_ready:
        raise ContainmentUnavailableError("browser containment is unavailable on this worker")
    return capability
