"""Exception types shared across the pipeline."""


class WbsError(Exception):
    """Base class for expected, user-facing failures."""


class ToolMissing(WbsError):
    """A required external executable (Blender, ffmpeg, ffprobe) could not be located."""


class TransientError(WbsError):
    """A failure worth a bounded retry (network timeout, HTTP 429/5xx)."""


class ProviderNotConfigured(WbsError):
    """A real provider was selected but its endpoint or credential variables are not set."""


class PaidCallNotConfirmed(WbsError):
    """A real (possibly paid) model call was attempted without explicit confirmation."""


class BudgetExceeded(WbsError):
    """The per-job or per-batch spending cap would be exceeded."""


class ContractError(WbsError):
    """A data contract (scene.json, SP输出_v3.json, manifests) failed validation."""
