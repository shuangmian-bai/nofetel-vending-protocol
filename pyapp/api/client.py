"""诺丰泰小程序协议还原 —— HTTP 客户端底座。

职责：
1. 复刻客户端 `utils/api.js` 的请求头集合（app-id / user-id / version / qr-code / scene-code / page-path）；
2. 统一从密钥库取 openId（经环境变量注入，绝不落盘、绝不进日志）；
3. 对写操作强制 confirm 门禁与金额闸门（project-conventions 03 分册「写操作只做存在性探测」）。

被谁调用：pyapp/api/* 下的各业务模块。
不负责：业务字段解析（那是各 api 模块的事）。

依据：data/samples/ 下的原始请求样本 + 小程序 utils/api.js 的封装逻辑。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

logger = logging.getLogger(__name__)

# 客户端未做任何签名：仅靠 app-id / user-id / version 三个头。
# 依据：utils/api.js 的 header 构造（无 sign、无 timestamp、无 nonce）。
DEFAULT_HEADERS = {
    "content-type": "application/json",
}

# 小程序请求超时 10s（app.json -> networkTimeout.request）
DEFAULT_TIMEOUT = 10.0


class ApiError(RuntimeError):
    """业务层返回 code != 200，或传输层失败。"""

    def __init__(self, message: str, *, code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload


class ConfirmRequired(RuntimeError):
    """写操作缺少显式确认。调用方必须向人确认后以 confirm=True 重试。"""


@dataclass
class NoftelConfig:
    """运行参数。全部来自环境变量，凭据只取名字不取值。"""

    dev_id: str
    app_id: str = "wxd005d05c066d1e3e"
    client_version: str = "V3.8.0-20250613"
    base_url: str = "https://1.nofetel.com/VendingSystem"
    max_order_amount: float = 50.0
    pay_poll_interval: float = 1.5
    pay_poll_max: int = 100
    # BLE 特征默认留空 = 连接后自动发现（推荐）。
    # 值来自 pages/index/index.js 的 getBLEDeviceServices 逻辑：
    # 取 uuid 含 FEE0/FFF0/FF00/FE1691 的主服务，再在其中找带 notify 与 write 的特征。
    ble_service: str = ""
    ble_notify: str = ""
    ble_write: str = ""
    # 身份标识（openId）。两种来源，按优先级：
    #   1) 环境变量 NOFETEL_OPENID（推荐：不落盘，适合共享机器）
    #   2) config/.env 里的 NOFETEL_OPENID（简单，但要求该文件的 chmod 真实生效）
    # repr=False：避免日志/异常里打印整个配置对象时泄漏
    open_id: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "NoftelConfig":
        env = os.environ
        dev_id = env.get("NOFETEL_DEVID", "").strip()
        if not dev_id:
            raise ApiError(
                "缺少设备主键 devId。三种注入方式（优先级由高到低）：\n"
                "  1) 命令行：--dev-id <DEVICE_ID>\n"
                "  2) 环境变量：NOFETEL_DEVID=<DEVICE_ID> .venv/bin/python main.py info\n"
                "  3) 配置文件：在 config/.env 里填写 NOFETEL_DEVID="
            )
        return cls(
            dev_id=dev_id,
            app_id=env.get("NOFETEL_APP_ID", cls.app_id),
            client_version=env.get("NOFETEL_CLIENT_VERSION", cls.client_version),
            base_url=env.get("NOFETEL_BASE_URL", cls.base_url).rstrip("/"),
            max_order_amount=float(env.get("NOFETEL_MAX_ORDER_AMOUNT", cls.max_order_amount)),
            pay_poll_interval=float(env.get("NOFETEL_PAY_POLL_INTERVAL", cls.pay_poll_interval)),
            pay_poll_max=int(env.get("NOFETEL_PAY_POLL_MAX", cls.pay_poll_max)),
            ble_service=env.get("NOFETEL_BLE_SERVICE", cls.ble_service),
            ble_notify=env.get("NOFETEL_BLE_NOTIFY", cls.ble_notify),
            ble_write=env.get("NOFETEL_BLE_WRITE", cls.ble_write),
            open_id=env.get("NOFETEL_OPENID", "").strip(),
        )


@dataclass
class NoftelClient:
    """单一请求出口。

    openId 取值顺序：
      1) 调用方显式传入的 open_id 参数；
      2) 环境变量 NOFETEL_OPENID（推荐，不落盘）；
      3) config/.env 里的 NOFETEL_OPENID（由 NoftelConfig 读入）。
    代码里不放任何默认值，避免凭据硬编码。
    """

    config: NoftelConfig
    open_id: str = field(default="", repr=False)  # repr=False：防止日志打印对象时泄漏
    qr_code: str = ""
    _http: httpx.Client | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.open_id:
            self.open_id = os.environ.get("NOFETEL_OPENID", "").strip() or self.config.open_id
        if not self.qr_code:
            self.qr_code = f"https://c.youzhuyougou.com/mini/1/?devid={self.config.dev_id}"
        self._http = httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def __enter__(self) -> "NoftelClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 内部：请求头 ----

    def _headers(self, *, with_token: bool = False, page_path: str = "pages/index/index") -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        headers["app-id"] = self.config.app_id
        headers["version"] = self.config.client_version
        headers["user-id"] = self.open_id
        headers["qr-code"] = self.qr_code
        headers["scene-code"] = "1011"  # 扫码进入（客户端 wx.getLaunchOptionsSync().scene）
        headers["page-path"] = page_path
        if with_token:  # 客户端仅少数接口带 accessToken，本还原链路未用到
            headers["accessToken"] = ""
        return headers

    # ---- 对外：读操作 ----

    def get(self, path: str, params: Mapping[str, Any] | None = None, *, page_path: str = "pages/index/index") -> Any:
        """只读 GET。不做确认门禁——读操作不改变任何状态。"""
        url = f"{self.config.base_url}{path}"
        logger.debug("GET %s params=%s", path, sorted((params or {}).keys()))
        try:
            resp = self._http.get(url, params=dict(params or {}), headers=self._headers(page_path=page_path))
        except httpx.HTTPError as exc:
            raise ApiError(f"请求失败 {path}: {exc}") from exc
        return self._parse(path, resp)

    # ---- 对外：写操作（门禁） ----

    def post(
        self,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        query: str = "",
        confirm: bool = False,
        amount: float | None = None,
        page_path: str = "pages/index/index",
    ) -> Any:
        """写操作 POST。

        双重门禁：
        1. confirm 必须显式为 True（默认 False -> 抛 ConfirmRequired）；
        2. amount 不为空时必须 <= config.max_order_amount（金额闸门，防误发大额订单）。

        依据 project-conventions/03 分册：「支付类在库层就要求 confirm=True 且金额有闸门值」。
        """
        if not confirm:
            raise ConfirmRequired(f"写操作 {path} 需要确认：以 confirm=True 调用才会真正发出")
        if amount is not None and amount > self.config.max_order_amount:
            raise ConfirmRequired(
                f"金额 {amount:.2f} 超过闸门 {self.config.max_order_amount:.2f}，拒绝发出订单"
            )
        url = f"{self.config.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        logger.info("POST %s（已确认，金额=%s）", path, amount)
        try:
            resp = self._http.post(
                url,
                json=dict(payload or {}),
                headers=self._headers(page_path=page_path),
            )
        except httpx.HTTPError as exc:
            raise ApiError(f"请求失败 {path}: {exc}") from exc
        return self._parse(path, resp)

    # ---- 内部：响应解析 ----

    @staticmethod
    def _parse(path: str, resp: httpx.Response) -> Any:
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"{path} 返回非 JSON（HTTP {resp.status_code}）", payload=resp.text[:400]) from exc
        code = data.get("code")
        if code != 200:
            raise ApiError(
                f"{path} 业务失败：{data.get('message') or data.get('msg') or '未知错误'}",
                code=code,
                payload=data,
            )
        return data.get("result")

    # ---- 工具 ----

    def sleep_poll(self) -> None:
        time.sleep(self.config.pay_poll_interval)
