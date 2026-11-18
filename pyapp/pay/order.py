"""下单与支付状态（写操作 + 读轮询）。

还原自 pages/index/index.js 的三段调用：
  handleCreatedOrder -> /front/order/addOrder          （POST，创建订单）
  getPayUrl          -> /front/smallWxpay/unifiedOrder （POST，换取 wx.requestPayment 参数）
  getPayStatus       -> /front/smallWxpay/getPayStatus （GET 轮询，支付成功后返回 payCode）

关键结论（来自客户端代码）：
- 开柜指令 payCode 由服务端在支付成功后下发，客户端只做透传，不自行计算；
- 轮询到 status ∈ {1, 12} 才停止并走开柜；5001 表示购物车购买时蓝牙断开；
- 轮询间隔 1500ms，最多 100 次。

写操作全部经 NoftelClient.post 的 confirm + 金额闸门。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from pyapp.api.client import ApiError, NoftelClient

logger = logging.getLogger(__name__)

# 支付成功状态码（pages/index/index.js: 1 != status && 12 != status 才继续轮询）
PAID_STATUS = (1, 12)
# 购物车购买时蓝牙断开（客户端特判 5001）
BLUETOOTH_LOST = 5001


@dataclass
class CreatedOrder:
    """addOrder 的返回。"""

    order_id: str
    raw: dict[str, Any]


@dataclass
class PayStatus:
    """getPayStatus 的一次返回。"""

    status: int | None
    pay_code: str
    open_password: str  # 密码开柜机型的开柜密码（服务端下发），蓝牙机型为空
    raw: dict[str, Any]

    @property
    def paid(self) -> bool:
        return self.status in PAID_STATUS


def build_order_payload(dev_id: str, items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """构造下单体。

    客户端结构（pages/index/index.js:70 orderInfo）：
        {devId: "", items: [{itemId, cellId, sellStatus, sellNum, itemNo}, ...]}
    """
    return {"devId": dev_id, "items": [dict(it) for it in items]}


def calculate_amount(items: Iterable[Mapping[str, Any]], prices: Mapping[int, float]) -> float:
    """按货道价格与数量算合计，用于金额闸门。

    prices: {cellId: 单价}。注意服务端会二次核价，这里只用于闸门，不作为下单依据。
    """
    total = 0.0
    for it in items:
        cell = int(it.get("cellId") or 0)
        qty = int(it.get("sellNum") or 1)
        total += float(prices.get(cell, 0.0)) * qty
    return round(total, 2)


def create_order(
    client: NoftelClient,
    items: list[Mapping[str, Any]],
    *,
    amount: float,
    confirm: bool,
) -> CreatedOrder:
    """创建订单（写操作，需 confirm）。

    客户端命中「操作太频繁啦，请3秒后再试」时无 orderId 返回，此处原样抛出。
    """
    payload = build_order_payload(client.config.dev_id, items)
    logger.info("创建订单：%d 个货道，金额 %.2f", len(payload["items"]), amount)
    result = client.post("/front/order/addOrder", payload, confirm=confirm, amount=amount)
    if not result or not result.get("orderId"):
        raise ApiError("下单未返回 orderId（客户端提示：操作太频繁啦，请3秒后再试）", payload=result)
    return CreatedOrder(order_id=str(result["orderId"]), raw=result)


def unified_order(client: NoftelClient, order_id: str, *, confirm: bool) -> dict[str, Any]:
    """换取微信支付参数（写操作：会在服务端预下单）。

    返回字段：timeStamp / nonceStr / packageValue / signType / paySign
    客户端把它喂给 wx.requestPayment；本还原把整包返回，交由真人完成支付或外部渠道支付。
    """
    result = client.post(
        "/front/smallWxpay/unifiedOrder",
        query=f"orderId={order_id}",
        confirm=confirm,
    )
    return dict(result or {})


def get_pay_status(client: NoftelClient, order_id: str, *, is_charge: int = 0, need_pwd: bool = False) -> PayStatus:
    """单次查询支付状态（只读）。"""
    result = client.get(
        "/front/smallWxpay/getPayStatus",
        {"orderId": order_id, "isCharge": is_charge, "isNeedPwd": "true" if need_pwd else "false"},
    )
    result = dict(result or {})
    status = result.get("status")
    return PayStatus(
        status=int(status) if status is not None else None,
        pay_code=str(result.get("payCode") or ""),
        # 服务端字段名固定为 "pwd"（协议契约），这里取的是响应值不是字面量口令；
        # 凭据扫描工具会按变量名报高危，属误报，已人工复核。
        open_password=str(result.get("pwd") or ""),
        raw=result,
    )


def wait_for_payment(client: NoftelClient, order_id: str, *, need_pwd: bool = False) -> PayStatus:
    """轮询直到支付成功。

    复刻客户端 getPayStatus：间隔 1.5s、最多 100 次；未支付不返回 payCode。
    轮询结束仍未支付则抛错，绝不放行开柜。
    """
    interval = client.config.pay_poll_interval
    max_tries = client.config.pay_poll_max
    for attempt in range(1, max_tries + 1):
        status = get_pay_status(client, order_id, need_pwd=need_pwd)
        logger.debug("轮询 %d/%d：status=%s", attempt, max_tries, status.status)
        if status.paid:
            logger.info("支付成功（status=%s）", status.status)
            return status
        if status.status == BLUETOOTH_LOST:
            raise ApiError("服务端报告蓝牙连接丢失（5001），无法继续自动开柜", payload=status.raw)
        time.sleep(interval)
    raise TimeoutError(f"轮询 {max_tries} 次仍未支付，订单 {order_id} 未完成")
