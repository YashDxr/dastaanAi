"""Which failures are worth retrying.

Getting this wrong is expensive in both directions: retrying a bad API key wastes
a minute per stage and buries the real cause, while not retrying a 429 throws away
a story the moment OpenAI rate-limits us.
"""

import httpx
import pytest
from daastaan_agent.gateway import _is_transient
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status_error(cls, code: int):
    response = httpx.Response(status_code=code, request=REQUEST)
    return cls("boom", response=response, body=None)


class TestTransientFailures:
    @pytest.mark.parametrize(
        "exc",
        [
            _status_error(RateLimitError, 429),
            _status_error(InternalServerError, 500),
            APIConnectionError(request=REQUEST),
            APITimeoutError(request=REQUEST),
        ],
    )
    def test_retried(self, exc):
        assert _is_transient(exc) is True


class TestPermanentFailures:
    @pytest.mark.parametrize(
        "exc",
        [
            _status_error(AuthenticationError, 401),
            _status_error(PermissionDeniedError, 403),
            _status_error(BadRequestError, 400),
        ],
    )
    def test_not_retried(self, exc):
        """A bad key fails the same way on attempt four as on attempt one."""
        assert _is_transient(exc) is False

    def test_unexpected_exceptions_not_retried(self):
        assert _is_transient(ValueError("model returned nothing parseable")) is False
