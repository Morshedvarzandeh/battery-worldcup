"""Where the source of a running deployment can be obtained.

The AGPL asks something of a network service that a permissive licence does
not: users interacting with it remotely must be offered its Corresponding
Source. That is not a footnote here, it is the mechanism the whole thing rests
on — a valuation is only worth what the method behind it can be checked
against, and a service that will not show its method is asking to be taken on
faith.

So the source location is a runtime value rather than a hard-coded string. A
deployment running a modified engine sets ``BV_SOURCE_URL`` to its own tree and
becomes compliant; one that does not is publishing this repository's address
for code that is no longer this repository's, which is the failure mode worth
making easy to avoid.
"""

from __future__ import annotations

import os

LICENCE = "AGPL-3.0-or-later"
UPSTREAM_SOURCE_URL = "https://github.com/Morshedvarzandeh/battery-worldcup"
ENV_SOURCE_URL = "BV_SOURCE_URL"

_MODIFIED_NOTE = (
    "This deployment declares its own source tree, so it may differ from "
    "upstream. Both are shown."
)
_UPSTREAM_NOTE = (
    "This deployment has not declared a source location, so the upstream "
    "repository is offered. An operator running modified code should set "
    f"{ENV_SOURCE_URL}."
)


def source_url() -> str:
    """The tree this deployment claims to be running."""
    declared = (os.environ.get(ENV_SOURCE_URL) or "").strip()
    return declared or UPSTREAM_SOURCE_URL


def offer() -> dict[str, str]:
    """The source offer, as served over HTTP and printed by the CLI."""
    declared = source_url()
    modified = declared != UPSTREAM_SOURCE_URL
    return {
        "licence": LICENCE,
        "source_url": declared,
        "upstream_url": UPSTREAM_SOURCE_URL,
        "note": _MODIFIED_NOTE if modified else _UPSTREAM_NOTE,
    }


__all__ = [
    "ENV_SOURCE_URL",
    "LICENCE",
    "UPSTREAM_SOURCE_URL",
    "offer",
    "source_url",
]
