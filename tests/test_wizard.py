"""交互向导的离线单测。

不需要网络、不需要设备：用假的 DeviceSnapshot 与输入序列驱动 `_loop`，
验证购物车算术、售罄拦截、库存校验、金额闸门与取消路径。

运行：.venv/bin/python -m pytest tests/ -q
或：  .venv/bin/python tests/test_wizard.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyapp.api.client import NoftelConfig  # noqa: E402
from pyapp.api.device import CellItem, DeviceInfo, DeviceModel  # noqa: E402
from pyapp.cli import wizard  # noqa: E402
from pyapp.purchase import DeviceSnapshot  # noqa: E402


def make_snapshot(cells: list[dict[str, Any]], status: int = 1) -> DeviceSnapshot:
    return DeviceSnapshot(
        device=DeviceInfo(raw={"devId": "TESTDEV", "status": status, "hotelId": "H1", "roomCode": "101"}),
        model=DeviceModel(raw={"model": "N300-4", "baseModel": "T300", "cellNum": len(cells)}),
        cells=[CellItem(raw=c) for c in cells],
    )


DEFAULT_CELLS = [
    {"cellId": 1, "itemId": "i1", "itemNo": "n1", "itemName": "商品甲", "itemPrice": 10.0, "itemNum": 2},
    {"cellId": 2, "itemId": "i2", "itemNo": "n2", "itemName": "商品乙", "itemPrice": 5.5, "itemNum": 0},
]


class FakeClient:
    """只提供向导用到的 config；不实现任何网络方法。"""

    def __init__(self, max_amount: float = 100.0) -> None:
        self.config = NoftelConfig(dev_id="TESTDEV", max_order_amount=max_amount)


def run(script: list[str], snap: DeviceSnapshot | None = None, max_amount: float = 100.0) -> tuple[int, list[str]]:
    """用脚本化输入跑一遍向导，返回 (退出码, 输出行)。"""
    lines: list[str] = []
    inputs = iter(script)

    def fake_input(prompt: str) -> str:
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError from None

    # 拦截真实下单，记录调用参数
    sent: dict[str, Any] = {}

    def fake_purchase(client: Any, **kwargs: Any) -> Any:
        sent.update(kwargs)
        raise RuntimeError("测试中不下单")

    orig = wizard.purchase_and_open
    wizard.purchase_and_open = fake_purchase
    try:
        code = wizard.interactive_main(
            FakeClient(max_amount),
            input_fn=fake_input,
            out=lines.append,
            do_open=False,
            snap=snap,  # 注入快照，测试不触网
        )
    finally:
        wizard.purchase_and_open = orig

    # 把"是否真的下单"暴露给调用方
    run.sent = sent  # type: ignore[attr-defined]
    return code, lines


run.sent = {}  # type: ignore[attr-defined]


def test_cart_arithmetic() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    cart = wizard.Cart()
    cart.add(1, 1)
    cart.add(1, 1)
    assert cart.items == {1: 2}
    assert cart.total(snap.price_map()) == 20.0
    cart.set(1, 0)
    assert not cart


def test_sold_out_rejected() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    # 选序号 2（售罄），再加序号 9（不存在）
    code, lines = run(["2", "2,9", "5", "0"], snap)
    text = "\n".join(lines)
    assert code == 0
    assert "已售罄" in text
    assert "无效" in text
    assert "购物车是空的" in text


def test_add_and_show_cart() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    code, lines = run(["2", "1", "5", "0"], snap)
    text = "\n".join(lines)
    assert code == 0
    assert "已加入：商品甲" in text
    assert "10.00" in text


def test_qty_over_stock_rejected() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    code, lines = run(["2", "1", "3", "1", "9", "0"], snap)
    text = "\n".join(lines)
    assert code == 0
    assert "库存只有" in text


def test_amount_limit_blocks_checkout() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    # 买 2 件 10 元 = 20，闸门 5 元
    code, lines = run(["2", "1", "2", "1", "6"], snap, max_amount=5.0)
    text = "\n".join(lines)
    assert code == 2, f"应被金额闸门拦下，实际 {code}"
    assert "超过单笔上限" in text
    assert not run.sent, "金额超限时不应调用下单"


def test_checkout_cancel() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    code, lines = run(["2", "1", "6", "no", "0"], snap)
    text = "\n".join(lines)
    assert code == 0
    assert "已取消，未创建订单" in text
    assert not run.sent, "取消时不应调用下单"


def test_checkout_confirm_requires_yes() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    code, lines = run(["2", "1", "6", "YES"], snap)
    text = "\n".join(lines)
    # 输入大写的 YES 应当被判为确认（代码用 lower()）→ 会调用 fake_purchase
    assert run.sent, "确认后应调用下单"
    assert run.sent.get("confirm") is True
    assert "支付" in text or code == 1


def test_empty_cart_checkout_blocked() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    code, lines = run(["6", "0"], snap)
    text = "\n".join(lines)
    assert code == 0
    assert "购物车是空的" in text
    assert not run.sent


def test_device_not_purchasable() -> None:
    snap = make_snapshot(DEFAULT_CELLS, status=0)
    code, lines = run(["0"], snap)
    text = "\n".join(lines)
    assert code == 1
    assert "不可购买" in text or "不能购买" in text


def test_eof_aborts_safely() -> None:
    snap = make_snapshot(DEFAULT_CELLS)
    code, lines = run([], snap)
    assert code == 130
    assert not run.sent, "中断不应下单"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[通过] {name}")
            except AssertionError as exc:
                failed += 1
                print(f"[失败] {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[失败] {name}: {type(exc).__name__}: {exc}")
    print(f"\n合计失败 {failed} 项")
    raise SystemExit(1 if failed else 0)
