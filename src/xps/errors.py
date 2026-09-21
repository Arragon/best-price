"""统一错误码（指南 §8.6）与 HTTP 映射（spec §2.7）。

`200 + []` 只允许表示经核验的真实无结果，绝不用来表示上游失败。
"""

from __future__ import annotations

INVALID_QUERY = "INVALID_QUERY"
UNSUPPORTED_FILTER = "UNSUPPORTED_FILTER"
AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_EXPIRED = "AUTH_EXPIRED"
CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"
RATE_LIMITED = "RATE_LIMITED"
UPSTREAM_CHANGED = "UPSTREAM_CHANGED"
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
DB_ERROR = "DB_ERROR"
NO_VALID_RESULTS = "NO_VALID_RESULTS"
RUN_INTERRUPTED = "RUN_INTERRUPTED"

ALL_CODES = frozenset(
    {
        INVALID_QUERY,
        UNSUPPORTED_FILTER,
        AUTH_REQUIRED,
        AUTH_EXPIRED,
        CHALLENGE_REQUIRED,
        RATE_LIMITED,
        UPSTREAM_CHANGED,
        UPSTREAM_TIMEOUT,
        UPSTREAM_UNAVAILABLE,
        DB_ERROR,
        NO_VALID_RESULTS,
        RUN_INTERRUPTED,
    }
)

_HTTP_STATUS = {
    INVALID_QUERY: 422,
    UNSUPPORTED_FILTER: 422,
    AUTH_REQUIRED: 401,
    AUTH_EXPIRED: 401,
    CHALLENGE_REQUIRED: 409,
    RATE_LIMITED: 429,
    UPSTREAM_CHANGED: 502,
    UPSTREAM_TIMEOUT: 504,
    UPSTREAM_UNAVAILABLE: 503,
    DB_ERROR: 500,
    NO_VALID_RESULTS: 200,
    RUN_INTERRUPTED: 200,
}

_RETRYABLE = {
    INVALID_QUERY: False,
    UNSUPPORTED_FILTER: False,
    AUTH_REQUIRED: False,
    AUTH_EXPIRED: False,
    CHALLENGE_REQUIRED: False,
    RATE_LIMITED: True,
    UPSTREAM_CHANGED: False,
    UPSTREAM_TIMEOUT: True,
    UPSTREAM_UNAVAILABLE: True,
    DB_ERROR: True,
    NO_VALID_RESULTS: False,
    RUN_INTERRUPTED: False,
}

_REQUIRES_HUMAN = {
    INVALID_QUERY: False,
    UNSUPPORTED_FILTER: False,
    AUTH_REQUIRED: True,
    AUTH_EXPIRED: True,
    CHALLENGE_REQUIRED: True,
    RATE_LIMITED: True,
    UPSTREAM_CHANGED: True,
    UPSTREAM_TIMEOUT: False,
    UPSTREAM_UNAVAILABLE: False,
    DB_ERROR: False,
    NO_VALID_RESULTS: False,
    RUN_INTERRUPTED: False,
}

# 出现即必须停止自动操作，交还用户（指南 §9 失败策略）
HALT_CODES = frozenset({CHALLENGE_REQUIRED, RATE_LIMITED, AUTH_REQUIRED})


def http_status(code: str) -> int:
    return _HTTP_STATUS.get(code, 500)


class ServiceError(Exception):
    """带机器可读错误码的业务异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        run_id: str | None = None,
        retryable: bool | None = None,
        requires_human_action: bool | None = None,
    ) -> None:
        super().__init__(message)
        if code not in ALL_CODES:
            raise ValueError(f"未登记的错误码：{code}")
        self.code = code
        self.message = message
        self.run_id = run_id
        self.retryable = _RETRYABLE[code] if retryable is None else retryable
        self.requires_human_action = (
            _REQUIRES_HUMAN[code] if requires_human_action is None else requires_human_action
        )

    @property
    def status_code(self) -> int:
        return http_status(self.code)

    def to_body(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "run_id": self.run_id,
            "retryable": self.retryable,
            "requires_human_action": self.requires_human_action,
        }


class UpstreamError(ServiceError):
    """上游采集器/闲鱼平台返回的失败。"""


class StorageError(ServiceError):
    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(DB_ERROR, message, run_id=run_id)
