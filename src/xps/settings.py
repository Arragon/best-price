"""环境变量与配置校验（指南 §9）。

安全默认：只监听 loopback。要跨设备访问必须显式 ALLOW_REMOTE_ACCESS=true，
且指南 §9 要求优先用 Tailscale / SSH 隧道，而不是「改成 0.0.0.0 就宣布安全」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from xps.adapters.xianyu import DEFAULT_UPSTREAM_PATH

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8765
    database_path: Path = Path("data/price.sqlite3")

    xianyu_adapter: Literal["upstream"] = "upstream"
    xianyu_upstream_path: Path = DEFAULT_UPSTREAM_PATH
    # 落库到 search_runs.source_commit，用于事后判断某轮数据是哪个上游版本抓的
    xianyu_source_commit: str | None = None
    # scripts/setup.sh 写入；显式配置优先于此文件
    upstream_commit_file: Path = Path("data/upstream-commit.txt")

    max_search_pages: int = 3
    max_concurrent_searches: int = 1
    min_seconds_between_searches: float = 30.0
    seconds_between_pages: float = 3.0
    # Task-level pace is advisory. Until this explicit switch is enabled, the
    # legacy MIN_SECONDS_BETWEEN_SEARCHES remains the hard compatibility floor.
    allow_faster_pace: bool = False
    platform_floor_seconds: float = 10.0
    economy_gap_seconds: float = 60.0
    balanced_gap_seconds: float = 30.0
    fast_gap_seconds: float = 12.0
    max_pending_jobs: int = 12
    search_cache_ttl_seconds: int = 600
    rate_limit_cooldown_seconds: int = 300

    allow_remote_access: bool = False
    log_level: str = "INFO"
    log_file: Path | None = Path("data/logs/bestprice.log")
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 3

    # 有效样本少于此值即告警。MVP 阈值，不是统计学保证（指南 §7）
    min_sample_threshold: int = 8

    # Optional cloud text extraction. Disabled means deterministic rules-only analysis.
    ai_enabled: bool = False
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str | None = None
    ai_api_key: SecretStr | None = None
    ai_timeout_seconds: float = 30.0
    ai_max_input_chars: int = 6000

    @model_validator(mode="after")
    def _guard_bind_address(self) -> Settings:
        if not self.allow_remote_access and self.app_host not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"app_host={self.app_host!r} 不是 loopback 地址。默认只允许监听本机；"
                "确需跨设备访问请显式设置 ALLOW_REMOTE_ACCESS=true，并优先使用 "
                "Tailscale / SSH 隧道而不是直接暴露端口（指南 §9）。"
            )
        return self

    @model_validator(mode="after")
    def _guard_single_writer(self) -> Settings:
        # SQLite 单进程串行写；MVP 不支持并发采集，配置错了要立刻炸而不是静默忽略
        if self.max_concurrent_searches != 1:
            raise ValueError(
                f"max_concurrent_searches={self.max_concurrent_searches} 不受支持："
                "MVP 为单实例串行采集（SQLite 单写者 + 低频访问平台）。"
            )
        return self

    @model_validator(mode="after")
    def _guard_bounds(self) -> Settings:
        if self.max_search_pages < 1:
            raise ValueError("max_search_pages 必须 >= 1")
        if self.min_sample_threshold < 2:
            raise ValueError("min_sample_threshold 必须 >= 2")
        if self.max_pending_jobs < 1:
            raise ValueError("max_pending_jobs 必须 >= 1")
        if self.search_cache_ttl_seconds < 0:
            raise ValueError("search_cache_ttl_seconds 必须 >= 0")
        if min(
            self.platform_floor_seconds,
            self.economy_gap_seconds,
            self.balanced_gap_seconds,
            self.fast_gap_seconds,
        ) < 0:
            raise ValueError("搜索间隔不能为负数")
        if self.ai_enabled and (not self.ai_model or self.ai_api_key is None):
            raise ValueError("AI_ENABLED=true 时必须配置 AI_MODEL 与 AI_API_KEY")
        if self.ai_max_input_chars < 500:
            raise ValueError("ai_max_input_chars 必须 >= 500")
        if self.log_max_bytes < 1024:
            raise ValueError("log_max_bytes 必须 >= 1024")
        if self.log_backup_count < 0:
            raise ValueError("log_backup_count 必须 >= 0")
        return self

    def resolved_source_commit(self) -> str | None:
        """显式配置优先；否则读 setup.sh 记录的文件；都没有就返回 None，不猜。"""
        if self.xianyu_source_commit:
            return self.xianyu_source_commit
        try:
            recorded = self.upstream_commit_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return recorded or None
