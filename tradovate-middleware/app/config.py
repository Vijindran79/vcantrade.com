from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    tradovate_env: str = Field("demo", alias="TRADOVATE_ENV")
    tradovate_app_id: str = Field("", alias="TRADOVATE_APP_ID")
    tradovate_cid: str = Field("", alias="TRADOVATE_CID")
    tradovate_sec: str = Field("", alias="TRADOVATE_SEC")
    tradovate_app_version: str = Field("1.0", alias="TRADOVATE_APP_VERSION")

    tradovate_account_id: int | None = Field(None, alias="TRADOVATE_ACCOUNT_ID")
    tradovate_account_spec: str = Field("Trials", alias="TRADOVATE_ACCOUNT_SPEC")

    webhook_api_key: str = Field("", alias="WEBHOOK_API_KEY")
    webhook_path: str = Field("/webhook/tradingview", alias="WEBHOOK_PATH")

    # TradingView cannot set custom headers on an alert webhook, so the shared
    # secret may also travel in the URL (path segment or ?key=). Disable this
    # once a reverse proxy injects X-API-Key.
    allow_url_key: bool = Field(True, alias="ALLOW_URL_KEY")
    tradingview_ip_allowlist: str = Field("", alias="TRADINGVIEW_IP_ALLOWLIST")

    # DRY_RUN: exercise the full signal path with a simulated broker. No
    # credentials required, no order ever reaches Tradovate.
    dry_run: bool = Field(False, alias="DRY_RUN")

    max_daily_loss_usd: float = Field(1500.0, alias="MAX_DAILY_LOSS_USD")
    trailing_drawdown_usd: float = Field(2000.0, alias="TRAILING_DRAWDOWN_USD")
    max_contracts_per_order: int = Field(2, alias="MAX_CONTRACTS_PER_ORDER")
    max_total_open_positions: int = Field(2, alias="MAX_TOTAL_OPEN_POSITIONS")
    duplicate_signal_window_sec: int = Field(5, alias="DUPLICATE_SIGNAL_WINDOW_SEC")

    default_stop_ticks: int = Field(40, alias="DEFAULT_STOP_TICKS")
    default_tp_ticks: int = Field(80, alias="DEFAULT_TP_TICKS")
    tick_size_nq: float = Field(0.25, alias="TICK_SIZE_NQ")
    tick_value_nq: float = Field(20.0, alias="TICK_VALUE_NQ")
    require_explicit_stop: bool = Field(True, alias="REQUIRE_EXPLICIT_STOP")

    host: str = Field("0.0.0.0", alias="HOST")
    port: int = Field(8080, alias="PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    state_file: Path = Field(Path("./state/risk_state.json"), alias="STATE_FILE")
    kill_switch_file: Path = Field(Path("./state/KILL_SWITCH"), alias="KILL_SWITCH_FILE")
    token_refresh_margin_sec: int = Field(300, alias="TOKEN_REFRESH_MARGIN_SEC")

    @property
    def tradovate_base_url(self) -> str:
        return (
            "https://demo.tradovateapi.com"
            if self.tradovate_env.lower() == "demo"
            else "https://live.tradovateapi.com"
        )

    @property
    def tradovate_md_url(self) -> str:
        return (
            "wss://md-demo.tradovateapi.com"
            if self.tradovate_env.lower() == "demo"
            else "wss://md.tradovateapi.com"
        )

    @property
    def ip_allowlist(self) -> list[str]:
        """Comma-separated source IPs allowed to use URL-embedded keys.

        Empty list = no IP restriction. TradingView publishes its webhook egress
        ranges; pinning them means a leaked alert URL is useless off-VPN.

        An inline comment is stripped rather than treated as an entry: a
        commented-out allowlist means "no restriction", and the alternative --
        silently rejecting *every* URL-key webhook from a valid IP -- is the
        worst possible way to find out about a stray ``#`` in a .env file.
        """
        raw = self.tradingview_ip_allowlist.split("#", 1)[0]
        return [ip.strip() for ip in raw.split(",") if ip.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
