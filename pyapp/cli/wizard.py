"""交互式购物向导：浏览商品 -> 加入购物车 -> 确认下单 -> 支付 -> 开柜。

与 `pyapp/cli` 的参数式命令并存：参数式适合脚本与复现，向导适合人工操作。
两者共用同一条编排链路（`pyapp.purchase`），门禁完全一致。

设计要点：
- 全程用序号选择，不让用户敲货道号与设备 ID；
- 购物车可反复增删，下单前展示明细与合计；
- 最后的确认步骤要求输入 `yes`，这是主链路上第二道人工闸门；
- Ctrl-C 在任何一步都安全退出，不会留下半截订单（下单是最后一步才发）。

副作用：仅在用户确认后才创建订单；开柜还要等支付成功。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable

from pyapp.api.client import ConfirmRequired, NoftelClient
from pyapp.api.device import CellItem
from pyapp.ble.opener import BleOpener
from pyapp.purchase import DeviceSnapshot, PurchaseResult, purchase_and_open, snapshot

# 输入函数可替换，便于测试时不依赖真实 stdin
InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


class UserAbort(Exception):
    """用户主动中断（Ctrl-C 或空输入按约定视为取消）。"""


@dataclass
class Cart:
    """购物车：货道号 -> 数量。保持插入顺序便于展示。"""

    items: dict[int, int] = field(default_factory=dict)

    def add(self, cell_id: int, qty: int = 1) -> None:
        self.items[cell_id] = self.items.get(cell_id, 0) + qty

    def set(self, cell_id: int, qty: int) -> None:
        if qty <= 0:
            self.items.pop(cell_id, None)
        else:
            self.items[cell_id] = qty

    def remove(self, cell_id: int) -> None:
        self.items.pop(cell_id, None)

    def clear(self) -> None:
        self.items.clear()

    def __bool__(self) -> bool:
        return bool(self.items)

    def total(self, price_map: dict[int, float]) -> float:
        return round(sum(price_map.get(c, 0.0) * n for c, n in self.items.items()), 2)


def _ask(prompt: str, input_fn: InputFn) -> str:
    try:
        return input_fn(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise UserAbort from None


def _render_cells(snap: DeviceSnapshot, out: OutputFn) -> None:
    """商品列表。售罄的也列出来但标注，避免用户以为"没有这件商品"。"""
    out("")
    out(f"{'序号':<5}{'商品':<30}{'单价':>9}{'库存':>7}")
    out("-" * 55)
    for idx, cell in enumerate(snap.cells, start=1):
        stock = cell.stock
        mark = "  (售罄)" if stock <= 0 else ""
        out(f"{idx:<5}{cell.name[:28]:<30}{cell.price:>9.2f}{stock:>7}{mark}")
    out("")


def _render_cart(snap: DeviceSnapshot, cart: Cart, out: OutputFn) -> None:
    price_map = snap.price_map()
    if not cart:
        out("  购物车是空的")
        return
    out(f"  {'商品':<30}{'单价':>9}{'数量':>5}{'小计':>10}")
    out("  " + "-" * 52)
    for cell_id, qty in cart.items.items():
        cell = snap.cell(cell_id)
        name = cell.name if cell else f"(货道{cell_id})"
        price = price_map.get(cell_id, 0.0)
        out(f"  {name[:28]:<30}{price:>9.2f}{qty:>5}{price * qty:>10.2f}")
    out("  " + "-" * 52)
    out(f"  {'合计':<44}{cart.total(price_map):>10.2f} 元")


def _pick_cell(snap: DeviceSnapshot, raw: str) -> tuple[int, CellItem] | None:
    """把用户输入解析为 (货道号, CellItem)。

    语义统一为**列表序号**（1 起），与 `_render_cells` 显示的序号一致。
    兼容性：序号超出范围时，再尝试按货道号精确匹配（货道号可能与序号不同）。
    """
    if not raw.isdigit():
        return None
    n = int(raw)
    if 1 <= n <= len(snap.cells):
        cell = snap.cells[n - 1]
        return cell.cell_id, cell
    cell = snap.cell(n)
    if cell is not None:
        return cell.cell_id, cell
    return None


def _checkout(
    client: NoftelClient,
    snap: DeviceSnapshot,
    cart: Cart,
    *,
    input_fn: InputFn,
    out: OutputFn,
    do_open: bool,
) -> int:
    """结算：确认 -> 下单 -> 支付轮询 -> 开柜。"""
    price_map = snap.price_map()
    total = cart.total(price_map)
    limit = client.config.max_order_amount

    out("")
    out("=" * 55)
    out("  订单确认")
    out("=" * 55)
    _render_cart(snap, cart, out)
    out("")
    if total > limit:
        out(f"[失败] 合计 {total:.2f} 元超过单笔上限 {limit:.2f} 元，已取消。")
        return 2

    ans = _ask(f"确认下单并支付 {total:.2f} 元？输入 yes 继续，其它任意键返回 > ", input_fn)
    if ans.lower() != "yes":
        out("已取消，未创建订单。")
        return 0

    try:
        result: PurchaseResult = purchase_and_open(
            client,
            cart=cart.items,
            confirm=True,
            do_open=do_open,
            snap=snap,
        )
    except ConfirmRequired as exc:
        out(f"[失败] {exc}")
        return 2
    except Exception as exc:
        out(f"[失败] {exc}")
        return 1

    out("")
    out("=" * 55)
    out(f"  订单号 {result.order.order_id}")
    out(f"  支付状态 {result.pay.status}")
    if result.open_result:
        out(f"  开柜 {'成功（已收到设备回包）' if result.open_result.notified else '未收到回包'}"
            f"，发送 {result.open_result.sent_frames} 帧")
    else:
        out("  开柜 已跳过")
    out("=" * 55)
    return 0


def interactive_main(
    client: NoftelClient,
    *,
    input_fn: InputFn = input,
    out: OutputFn = print,
    do_open: bool = True,
    snap: DeviceSnapshot | None = None,
) -> int:
    """向导外壳：把购物循环与结算串起来，统一处理中断。

    中断在任何阶段（含支付轮询）都安全：已创建的订单留在服务端，
    未支付的订单不会开柜，用户可在小程序「我的订单」里查看或放弃。
    """
    try:
        return _loop(client, input_fn=input_fn, out=out, do_open=do_open, snap=snap)
    except UserAbort:
        out("")
        out("已中断，未创建新订单。")
        return 130
    except KeyboardInterrupt:
        out("")
        out("已中断。")
        return 130


def _loop(
    client: NoftelClient,
    *,
    input_fn: InputFn,
    out: OutputFn,
    do_open: bool,
    snap: DeviceSnapshot | None = None,
) -> int:
    """购物主循环。snap 可注入，供测试跳过网络。"""
    snap = snap or snapshot(client)
    device, model = snap.device, snap.model
    ok, reason = device.can_purchase()

    out("=" * 55)
    out(f"  设备 {device.dev_id}")
    out(f"  型号 {model.model}（{model.cell_num} 货道）  房间 {device.room_code or '-'}")
    out(f"  状态 {device.status_text}    {'可购买' if ok else '不可购买：' + reason}")
    out("=" * 55)
    if not ok:
        out("[失败] 当前设备不能购买，已退出。")
        return 1
    if not snap.cells:
        out("[失败] 该设备没有可售货道。")
        return 1

    cart = Cart()
    menu = "[1] 商品列表  [2] 加入购物车  [3] 改数量  [4] 移除  [5] 查看购物车  [6] 结算  [0] 退出"

    while True:
        out("")
        out(menu)
        choice = _ask("请选择 > ", input_fn)

        if choice == "0":
            out("已退出，未创建任何订单。")
            return 0
        if choice == "1":
            _render_cells(snap, out)
        elif choice == "2":
            _render_cells(snap, out)
            raw = _ask("商品序号（可多个，逗号或空格分隔）> ", input_fn)
            if raw:
                for token in raw.replace("，", ",").replace(",", " ").split():
                    parsed = _pick_cell(snap, token)
                    if parsed is None:
                        out(f"  [注意] 序号 {token} 无效，跳过")
                        continue
                    cell_id, cell = parsed
                    if cell.stock <= 0:
                        out(f"  [注意] 「{cell.name}」已售罄，跳过")
                        continue
                    cart.add(cell_id, 1)
                    out(f"  已加入：{cell.name}")
        elif choice == "3":
            if not cart:
                out("  购物车是空的。")
                continue
            _render_cart(snap, cart, out)
            parsed = _pick_cell(snap, _ask("要修改的商品序号 > ", input_fn))
            if parsed is None or parsed[0] not in cart.items:
                out("  [注意] 该商品不在购物车中")
                continue
            cell_id, cell = parsed
            raw = _ask(f"新数量（1-{cell.stock}，0 移除）> ", input_fn)
            if not raw.isdigit():
                out("  [注意] 请输入数字")
                continue
            qty = int(raw)
            if qty > cell.stock:
                out(f"  [注意] 库存只有 {cell.stock}")
                continue
            cart.set(cell_id, qty)
            out("  已更新" if qty else "  已移除")
        elif choice == "4":
            if not cart:
                out("  购物车是空的。")
                continue
            _render_cart(snap, cart, out)
            parsed = _pick_cell(snap, _ask("要移除的商品序号 > ", input_fn))
            if parsed is None or parsed[0] not in cart.items:
                out("  [注意] 该商品不在购物车中")
                continue
            cart.remove(parsed[0])
            out("  已移除")
        elif choice == "5":
            _render_cart(snap, cart, out)
        elif choice == "6":
            if not cart:
                out("  购物车是空的，先加入商品。")
                continue
            code = _checkout(client, snap, cart, input_fn=input_fn, out=out, do_open=do_open)
            return code
        else:
            out("  [注意] 无效选择")
