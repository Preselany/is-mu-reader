"""Public error categories shared by Python, CLI and REST callers.

All categories remain catchable as ISMUError. Messages never contain upstream
response bodies, credentials or form tokens.
"""


class ISMUError(Exception):
    """Base class for expected client failures."""

    code = "ismu_error"


class AuthRequired(ISMUError):
    """The saved session is missing/expired or login needs user intervention."""

    code = "auth_required"


class InvalidInput(ISMUError):
    """Correct the supplied identifier, path, answer or option before retrying."""

    code = "invalid_input"


class UnsupportedOperation(ISMUError):
    """The requested route, control or action is outside the supported adapter."""

    code = "unsupported_operation"


class StateError(ISMUError):
    """Local session/attempt state is missing, stale or incompatible with the action."""

    code = "state_error"


class UpstreamError(ISMUError):
    """IS MU returned an error or an unusable response.

    status_code is the upstream HTTP status when one is available, not the
    status returned by the local REST service.
    """

    code = "upstream_error"

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class NetworkError(UpstreamError):
    """HTTPS failed or the response body was interrupted."""

    code = "network_error"


class RateLimited(UpstreamError):
    """IS MU returned HTTP 429; stop this collection and retry later."""

    code = "rate_limited"


class ParseError(UpstreamError):
    """The returned page does not match a supported structure.

    This can also mean an access-denied page; it does not prove markup changed.
    """

    code = "parse_error"


class AttemptUncertain(StateError):
    """A write may have happened. Inspect IS MU before any further write."""

    code = "attempt_uncertain"
