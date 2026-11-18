"""主链路编排：获取信息 -> 创建订单 -> 支付 -> 确认已支付 -> 开锁。

门禁设计（对应 project-conventions 03 分册「写操作只做存在性探测」）：

  创建订单  需要 confirm=True
  预下单    需要 confirm=True（会触发服务端预下单）
  BLE 开柜  必须在 getPayStatus 返回 status ∈ {1, 12} 且拿到 payCode 之后才允许
            —— 这是本项目最重要的闸门：拿不到服务端下发的 payCode 就不可能有开柜指令。

本模块不直接发 HTTP，也不直接碰蓝牙，只做时序编排与校验，便于单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from pyapp.api.client import ConfirmRequired, NoftelClient
from pyapp.api.device import CellItem, DeviceInfo, DeviceModel, get_cell_items, get_device_info, get_model_config
from pyapp.ble.opener import BleOpener, OpenResult
from pyapp.pay.order import (
    CreatedOrder,
    PayStatus,
    calculate_amount,
    create_order,
    unified_order,
    wait_for_payment,
)

logger = logging.getLogger(__name__)


@dataclass
class DeviceSnapshot:
    """一次性的设备全貌（只读）。"""

    device: DeviceInfo
    model: DeviceModel
    cells: list[CellItem]

    def price_map(self) -> dict[int, float]:
        return {c.cell_id: c.price for c in self.cells}

    def cell(self, cell_id: int) -> CellItem | None:
        for c in self.cells:
            if c.cell_id == cell_id:
                return c
        return None


def snapshot(client: NoftelClient) -> DeviceSnapshot:
    """拉取设备型号、归属、货道商品（全部只读）。"""
    model = get_model_config(client)
    info = get_device_info(client)
    cells = get_cell_items(client)
    return DeviceSnapshot(device=info, model=model, cells=cells)


@dataclass
class PurchaseResult:
    order: CreatedOrder
    pay: PayStatus
    payment_params: dict[str, Any]
    open_result: OpenResult | None


def validate_cart(snap: DeviceSnapshot, cart: Mapping[int, int]) -> tuple[list[dict[str, Any]], float]:
    """校验购物车并换算为下单 items。

    cart: {cellId: 数量}。返回 (下单 items, 合计金额)。

    逐个校验而不是整体校验，是为了在交互向导里能精确指出"哪一件有问题"。
    """
    if not cart:
        raise RuntimeError("购物车是空的")

    items: list[dict[str, Any]] = []
    for cell_id, qty in cart.items():
        if qty <= 0:
            raise RuntimeError(f"货道 {cell_id} 的数量必须大于 0")
        cell = snap.cell(cell_id)
        if cell is None:
            raise RuntimeError(f"货道 {cell_id} 不存在（本机共 {len(snap.cells)} 个货道）")
        if cell.stock <= 0:
            raise RuntimeError(f"货道 {cell_id}「{cell.name}」已售罄")
        if qty > cell.stock:
            raise RuntimeError(f"货道 {cell_id}「{cell.name}」库存不足：现有 {cell.stock}，要买 {qty}")
        items.append(cell.to_order_item(qty))

    return items, calculate_amount(items, snap.price_map())


def purchase_and_open(
    client: NoftelClient,
    *,
    cart: Mapping[int, int] | None = None,
    cell_id: int | None = None,
    qty: int = 1,
    confirm: bool = False,
    do_open: bool = True,
    ble: BleOpener | None = None,
    snap: DeviceSnapshot | None = None,
) -> PurchaseResult:
    """完整链路：下单 -> 预下单 -> 轮询支付 -> 开柜。

    两种入参方式：
      - cart={cellId: 数量}：购物车（交互向导用）
      - cell_id + qty：单件（脚本调用用，等价于 cart={cell_id: qty}）

    任何一步不满足前置条件都抛错并中止，绝不"猜一个 payCode"去开柜。
    """
    if cart is None:
        if cell_id is None:
            raise RuntimeError("必须提供 cart 或 cell_id")
        cart = {cell_id: qty}

    snap = snap or snapshot(client)
    ok, reason = snap.device.can_purchase()
    if not ok:
        raise RuntimeError(reason)

    items, amount = validate_cart(snap, cart)
    detail = "、".join(f"货道{c}:{n}件" for c, n in cart.items())
    logger.info("准备下单：%s，合计 %.2f 元", detail, amount)

    order = create_order(client, items, amount=amount, confirm=confirm)
    logger.info("订单已创建：%s", order.order_id)

    payment_params = unified_order(client, order.order_id, confirm=confirm)
    logger.info("已取得支付参数（signType=%s），请用 wx.requestPayment 或外部渠道完成支付", payment_params.get("signType"))

    pay = wait_for_payment(client, order.order_id)
    if not pay.pay_code:
        raise ConfirmRequired("服务端未返回 payCode，无法开柜（可能该机型走密码开柜，请检查 hasPwd）")

    open_result: OpenResult | None = None
    if do_open:
        opener = ble or BleOpener(
            client.config.ble_service, client.config.ble_notify, client.config.ble_write
        )
        open_result = opener.open_cell_sync(snap.device.bluetooth_id, pay.pay_code)
        logger.info("开柜完成：发送 %d 片，收到回包=%s", open_result.sent_frames, open_result.notified)

    return PurchaseResult(order=order, pay=pay, payment_params=payment_params, open_result=open_result)
