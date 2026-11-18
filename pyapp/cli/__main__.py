"""命令行界面：把链路拆成可单独验证的步骤。

设计原则：
- 只读命令（info / cells）随时可跑，不需要确认；
- 写命令（buy）必须显式 --confirm，且金额受闸门限制；
- 开柜只由 buy 内部在「支付成功且拿到 payCode」后触发，不提供单独的开柜命令，
  避免出现"绕过支付直接开柜"的入口。

用法见 README.md。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from pyapp.cli.wizard import interactive_main
from pyapp.api.client import ApiError, ConfirmRequired, NoftelConfig
from pyapp.api.client import NoftelClient
from pyapp.pay.order import get_pay_status
from pyapp.purchase import purchase_and_open, snapshot

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

logger = logging.getLogger(__name__)


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载（不引第三方依赖）。

    只处理 KEY=VALUE 与 # 注释；不覆盖已存在的环境变量
    （这样 vault use 注入的 VAULT_* 优先）。
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nofetel", description="诺丰泰售货机协议还原工具")
    p.add_argument("--env-file", default="config/.env", help="配置文件路径（默认 config/.env）")
    p.add_argument(
        "--dev-id",
        default="",
        help="设备主键，覆盖 .env 与环境变量（优先级：本参数 > 环境变量 > .env）",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    # 不指定子命令时进入交互式向导（推荐给人工使用）；
    # 指定子命令则是参数式调用（给脚本与复现用）。
    sub = p.add_subparsers(dest="cmd", required=False)

    sub.add_parser("interactive", help="交互式购物：浏览商品 -> 加购物车 -> 结算 -> 支付 -> 开柜")

    sub.add_parser("info", help="查看设备型号 / 归属 / 状态（只读）")
    sub.add_parser("cells", help="列出货道商品与价格（只读）")

    ps = sub.add_parser("pay-status", help="查询指定订单的支付状态（只读）")
    ps.add_argument("order_id")

    buy = sub.add_parser("buy", help="下单 -> 支付 -> 确认已支付 -> 开柜")
    buy.add_argument("--cell", type=int, required=True, help="货道号")
    buy.add_argument("--qty", type=int, default=1, help="数量（默认 1）")
    buy.add_argument("--confirm", action="store_true", help="确认执行写操作（下单与预下单）")
    buy.add_argument("--no-open", action="store_true", help="只到支付确认为止，不触发蓝牙开柜")
    return p


def cmd_info(client: NoftelClient) -> int:
    snap = snapshot(client)
    d, m = snap.device, snap.model
    ok, reason = d.can_purchase()
    print(f"设备        : {d.dev_id}")
    print(f"型号        : {m.model} / base {m.base_model}（{m.cell_num} 货道）")
    print(f"联网        : {'是' if m.connect_network else '否（离线，仅蓝牙可开门）'}")
    print(f"蓝牙        : {'支持' if m.connect_bluetooth else '不支持'}  ID={d.bluetooth_id}")
    print(f"密码开柜    : {'是' if m.has_pwd else '否'}")
    print(f"归属酒店    : {d.hotel_name or '(未填)'} / 房间 {d.room_code or '-'}")
    print(f"设备状态    : {d.status_text}")
    print(f"可购买      : {'是' if ok else f'否 - {reason}'}")
    return 0


def cmd_cells(client: NoftelClient) -> int:
    snap = snapshot(client)
    print(f"{'货道':<6}{'商品':<28}{'单价':>8}{'库存':>6}")
    print("-" * 50)
    for c in sorted(snap.cells, key=lambda x: x.cell_id):
        print(f"{c.cell_id:<6}{c.name[:26]:<28}{c.price:>8.2f}{c.stock:>6}")
    return 0


def cmd_pay_status(client: NoftelClient, order_id: str) -> int:
    st = get_pay_status(client, order_id)
    print(json.dumps({"status": st.status, "paid": st.paid, "has_pay_code": bool(st.pay_code)}, ensure_ascii=False))
    return 0


def cmd_buy(client: NoftelClient, args: argparse.Namespace) -> int:
    if not args.confirm:
        print("[失败] 写操作需要 --confirm 才会执行（下单与预下单都会真实发生）", file=sys.stderr)
        return 2
    result = purchase_and_open(
        client,
        cell_id=args.cell,
        qty=args.qty,
        confirm=True,
        do_open=not args.no_open,
    )
    print(json.dumps(
        {
            "order_id": result.order.order_id,
            "pay_status": result.pay.status,
            "opened": bool(result.open_result and result.open_result.notified),
            "frames_sent": result.open_result.sent_frames if result.open_result else 0,
        },
        ensure_ascii=False,
    ))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=LOG_FORMAT,
    )
    _load_dotenv(Path(args.env_file))

    try:
        config = NoftelConfig.from_env()
        if args.dev_id:
            # 命令行最高优先：允许多设备切换而不改文件
            config.dev_id = args.dev_id.strip()
            logger.info("设备主键由命令行指定：%s", config.dev_id)
        has_openid = bool(os.environ.get("VAULT_NOFETEL_OPENID"))
        if not has_openid:
            print(
                "[注意] 未检测到 VAULT_NOFETEL_OPENID，本次以匿名身份请求。\n"
                "       只读设备接口实测仍返回 200，但下单/支付/开柜必然失败。\n"
                "       正确用法：vault use nofetel_openid -- .venv/bin/python main.py <命令>",
                file=sys.stderr,
            )
            if args.cmd == "buy":
                print("[失败] 写操作必须有身份，请先用 vault use 注入 openId", file=sys.stderr)
                return 2
        with NoftelClient(config) as client:
            if args.cmd in (None, "interactive"):
                # 交互式向导是写操作路径，必须有身份
                if not has_openid:
                    print("[失败] 交互式下单需要身份，请先注入 openId 再运行", file=sys.stderr)
                    return 2
                return interactive_main(client)

            handlers = {
                "info": lambda: cmd_info(client),
                "cells": lambda: cmd_cells(client),
                "pay-status": lambda: cmd_pay_status(client, args.order_id),
                "buy": lambda: cmd_buy(client, args),
            }
            return handlers[args.cmd]()
    except ConfirmRequired as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, TimeoutError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
