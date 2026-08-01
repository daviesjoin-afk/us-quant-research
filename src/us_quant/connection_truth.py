from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class HealthLevel(StrEnum):
    UNKNOWN = "unknown"
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    key: str
    label: str
    level: HealthLevel
    detail: str
    verified_at: str

    @classmethod
    def now(
        cls,
        *,
        key: str,
        label: str,
        level: HealthLevel,
        detail: str,
    ) -> HealthCheck:
        return cls(
            key=key,
            label=label,
            level=level,
            detail=detail,
            verified_at=datetime.now(timezone.utc).isoformat(),
        )


@dataclass(frozen=True, slots=True)
class ConnectionTruth:
    socket: HealthCheck
    handshake: HealthCheck
    account_environment: HealthCheck
    market_data: HealthCheck
    safety: HealthCheck

    @property
    def all_ok(self) -> bool:
        return all(
            check.level == HealthLevel.OK
            for check in (
                self.socket,
                self.handshake,
                self.account_environment,
                self.market_data,
                self.safety,
            )
        )

    @property
    def trading_data_ready(self) -> bool:
        return (
            self.handshake.level == HealthLevel.OK
            and self.market_data.level == HealthLevel.OK
            and self.safety.level == HealthLevel.OK
        )


def initial_connection_truth() -> ConnectionTruth:
    unknown = HealthLevel.UNKNOWN
    return ConnectionTruth(
        socket=HealthCheck.now(
            key="socket",
            label="Gateway 端口",
            level=unknown,
            detail="尚未检查",
        ),
        handshake=HealthCheck.now(
            key="handshake",
            label="IBKR 协议",
            level=unknown,
            detail="尚未握手",
        ),
        account_environment=HealthCheck.now(
            key="account",
            label="账户环境",
            level=unknown,
            detail="尚未验证",
        ),
        market_data=HealthCheck.now(
            key="market_data",
            label="行情权限",
            level=unknown,
            detail="尚未订阅",
        ),
        safety=HealthCheck.now(
            key="safety",
            label="安全门",
            level=HealthLevel.OK,
            detail="本地只读配置；自动下单关闭",
        ),
    )
