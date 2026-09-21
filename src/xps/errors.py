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
HALT_CODES = frozenset({CHALLENGE_REQUIRED, RATE_LIMITED, AUTH_REQUIRED, AUTH_EXPIRED})

# 每个错误码对应「调用方（人或 agent）下一步该做什么」。
# 单一真相来源：scripts/query-price.sh 的失败渲染与 GET /help 共用这一份，
# 避免文档与实际行为各说一套。必须覆盖 ALL_CODES（有测试强制）。
AGENT_ACTIONS: dict[str, str] = {
    INVALID_QUERY: "请求参数不合法。检查关键词长度、max_pages 上限、价格区间与 sort 取值；"
    "404 则表示 run_id 不存在。",
    UNSUPPORTED_FILTER: "该筛选条件未经验证平台是否真过滤，已拒绝。去掉它，"
    "或先做一次人工交叉核对再决定是否开放。",
    AUTH_REQUIRED: "该操作需要登录态。请用户在本机运行 scripts/login.sh，"
    "然后 POST /v1/auth/reload。",
    AUTH_EXPIRED: "登录态已失效。请用户在本机运行 scripts/login.sh 重新扫码，"
    "然后 POST /v1/auth/reload（无需重启服务）。guest 搜索通常仍可继续。",
    CHALLENGE_REQUIRED: "平台要求人工验证。停止自动操作，请用户本人到闲鱼 App 或网页"
    "完成验证后再重试；不要自动重试，也不要尝试绕过。",
    RATE_LIMITED: "已触发平台频率限制。不要继续重试，也不要换账号或代理；"
    "请调大 MIN_SECONDS_BETWEEN_SEARCHES，由用户决定何时再跑。",
    UPSTREAM_CHANGED: "平台或上游接口结构可能已变。请重跑 scripts/verify_upstream.py "
    "核对字段路径，再更新解析层。",
    UPSTREAM_TIMEOUT: "上游超时。可有限次退避重试；不要并发重试造成放大。",
    UPSTREAM_UNAVAILABLE: "上游不可用。检查网络、upstream/ checkout 是否存在、"
    "依赖是否装齐（scripts/setup.sh）。",
    DB_ERROR: "本地数据库错误。查看服务日志；必要时用 data/backups/ 里的备份恢复。",
    NO_VALID_RESULTS: "本轮没有合格样本。查看 excluded_by_reason 与 needs_review_count，"
    "考虑换关键词或放宽 item_kind。",
    RUN_INTERRUPTED: "服务曾在本轮采集中途重启，该 run 已判定为中断。请重新发起搜索。",
}


def http_status(code: str) -> int:
    return _HTTP_STATUS.get(code, 500)


def is_retryable(code: str) -> bool:
    return _RETRYABLE.get(code, False)


def requires_human(code: str) -> bool:
    return _REQUIRES_HUMAN.get(code, False)


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
        status_code: int | None = None,
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
        self._status_code = status_code

    @property
    def status_code(self) -> int:
        # 允许覆盖：例如 run_id 不存在语义上是 INVALID_QUERY，但 HTTP 应当是 404
        return self._status_code or http_status(self.code)

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
