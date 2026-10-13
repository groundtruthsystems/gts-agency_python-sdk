"""Google ADK 2 support: durable agent sessions backed by the GTS session vault.

``GtsSessionService`` is an ADK ``BaseSessionService`` that persists every session in its GTS session
vault, so it survives restarts and moves between workers; ``DurableRunner`` adds correlation-id resume
and pauses for a human answer recorded in comand. Both build on ADK's own resumability rather than
replacing it. Design and backend contract: ``docs/agent_vault_gap_analysis.md``.

Requires the optional extra::

    pip install gts-agency-python-sdk[adk]

``import agency_sdk`` never imports this package (or ADK); only importing ``agency_sdk.adk`` does.
"""

_INSTALL_HINT = (
    "ADK session support requires the optional dependency group. Install it with:\n"
    "    pip install gts-agency-python-sdk[adk]"
)


class AdkNotInstalled(ImportError):
    """Raised when ``agency_sdk.adk`` is imported without the ``[adk]`` extra."""


try:
    import google.adk  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised in a subprocess without the extra
    raise AdkNotInstalled(_INSTALL_HINT) from exc

__all__ = ["AdkNotInstalled"]
