"""BLE 开柜（真机链路）。

还原自 pages/index/index.js 的 notycharacteristicsId + writeBLECharacteristicValue：

  1. 订阅 notify 特征（0000AE02），成功回调用 writeBLECharacteristicValue；
  2. 把 payCode（十六进制串）按 40 字符一片写入 write 特征（0000AE01）；
  3. 每片间隔 1 秒；尚未收到设备回的 notify 时重发，最多 3 次，超时报「开柜失败1003」。

关键约束：
- payCode 只能来自服务端（getPayStatus 返回），本地不生成；
- 部分机型用 hasPwd=1 走密码开柜，本模块只处理蓝牙机型（hasPwd=0）；
- 设备侧不会主动告诉我们"开了"，判定依赖 notify 回包（handleOpenCellCallBack）。

设备实测字段：blueToothId（如 <BLE_MAC_12HEX>）需拼成 BLE address 形态。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner

logger = logging.getLogger(__name__)

# 帧分片长度：客户端 datasubpck 按 40 个十六进制字符切（= 20 字节/片）
FRAME_CHUNK_HEX = 40
# 客户端未收到回包时的重发上限（notycharacteristicsId 中 o < 2 即最多 3 次）
MAX_RESEND = 3
# 每片之间的等待（客户端 setTimeout 1000ms）
CHUNK_INTERVAL = 1.0

# 设备名/地址前缀约定：蓝MAC 以这些开头时走直连
ADDRESS_PREFIXES = ("88", "AC", "C8", "D4", "DC")


class BleError(RuntimeError):
    """蓝牙链路异常（扫描不到、连接失败、写入失败）。"""


def normalize_ble_address(raw: str) -> str:
    """把服务端的 blueToothId 转成 BLE MAC 形态。

    实测 blueToothId="<BLE_MAC_12HEX>" -> "<BLE_MAC_COLON>"。
    若已经是带冒号/短横线的形态则原样返回。
    """
    value = (raw or "").strip().upper()
    if not value:
        raise BleError("设备信息里没有 blueToothId，无法走蓝牙开柜")
    if ":" in value or "-" in value:
        return value.replace("-", ":")
    if len(value) != 12:
        # 不是 12 位十六进制时原样交给 bleak，让它按 UUID/名称处理
        return value
    return ":".join(value[i : i + 2] for i in range(0, 12, 2))


def split_frames(payload_hex: str) -> list[str]:
    """按客户端 datasubpck 规则切片（40 个十六进制字符一片）。"""
    return [payload_hex[i : i + FRAME_CHUNK_HEX] for i in range(0, len(payload_hex), FRAME_CHUNK_HEX)]


def hex_to_bytes(chunk_hex: str) -> bytes:
    """十六进制串转字节。客户端是逐 2 字符 parseInt(16) 后 setInt8。"""
    if len(chunk_hex) % 2:
        chunk_hex = chunk_hex + "0"
    return bytes(int(chunk_hex[i : i + 2], 16) for i in range(0, len(chunk_hex), 2))


@dataclass
class OpenResult:
    """一次开柜尝试的结果。"""

    sent_frames: int
    notified: bool
    response_hex: str
    address: str


class BleOpener:
    """蓝牙开柜器：连接 -> 订阅 notify -> 写 payCode。"""

    def __init__(self, service_uuid: str, notify_uuid: str, write_uuid: str) -> None:
        self.service_uuid = service_uuid
        self.notify_uuid = notify_uuid
        self.write_uuid = write_uuid

    async def _find(self, address: str, timeout: float = 12.0) -> str:
        """确认设备可见并返回可连接的地址。"""
        logger.info("扫描蓝牙设备 %s（%.0fs）", address, timeout)
        target = address.upper()
        found = await BleakScanner.discover(timeout=timeout)
        for dev in found:
            addr = (dev.address or "").upper()
            name = (dev.name or "").upper()
            if addr == target or target in addr or target.replace(":", "") in addr.replace(":", ""):
                logger.info("找到设备 %s（名称 %s）", dev.address, dev.name)
                return dev.address
            if name and target.replace(":", "") in name.replace(":", ""):
                logger.info("按名称匹配到设备 %s（名称 %s）", dev.address, dev.name)
                return dev.address
        raise BleError(f"扫描 {timeout:.0f}s 未发现设备 {address}，请确认在设备附近且蓝牙已开启")

    async def open_cell(self, blue_tooth_id: str, pay_code_hex: str) -> OpenResult:
        """执行开柜。pay_code_hex 必须来自服务端。"""
        if not pay_code_hex:
            raise BleError("payCode 为空：只有支付成功后服务端才会下发开柜指令，拒绝开柜")

        address = normalize_ble_address(blue_tooth_id)
        address = await self._find(address)

        notified = asyncio.Event()
        response: dict[str, str] = {"hex": ""}

        def on_notify(_sender: object, data: bytearray) -> None:
            response["hex"] = data.hex()
            notified.set()
            logger.info("收到设备回包：%s", data.hex())

        async with BleakClient(address, timeout=20.0) as client:
            if not client.is_connected:
                raise BleError(f"连接设备 {address} 失败")

            logger.info("已连接 %s，订阅 notify %s", address, self.notify_uuid)
            await client.start_notify(self.notify_uuid, on_notify)

            frames = split_frames(pay_code_hex)
            sent = 0
            for attempt in range(1, MAX_RESEND + 1):
                for idx, chunk in enumerate(frames):
                    await client.write_gatt_char(self.write_uuid, hex_to_bytes(chunk), response=False)
                    sent += 1
                    logger.info("写入第 %d/%d 片：%s", idx + 1, len(frames), chunk)
                    await asyncio.sleep(CHUNK_INTERVAL)

                try:
                    await asyncio.wait_for(notified.wait(), timeout=3.0)
                    break
                except asyncio.TimeoutError:
                    if attempt == MAX_RESEND:
                        raise BleError("连续 3 次未收到设备回包，判定开柜失败1003") from None
                    logger.warning("本轮未收到回包，重发第 %d 次", attempt + 1)

            await client.stop_notify(self.notify_uuid)

        return OpenResult(sent_frames=sent, notified=notified.is_set(), response_hex=response["hex"], address=address)

    def open_cell_sync(self, blue_tooth_id: str, pay_code_hex: str) -> OpenResult:
        """同步入口（CLI 用）。"""
        return asyncio.run(self.open_cell(blue_tooth_id, pay_code_hex))
