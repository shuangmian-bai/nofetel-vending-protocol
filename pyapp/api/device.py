"""设备与货道信息（只读接口）。

对应小程序接口（utils/api.js）：
  /front/dev/getModelConfig  -> 机型配置（货道数、蓝牙/联网、是否有密码、补货策略）
  /front/dev/info            -> 设备归属（酒店、房间号、状态、蓝牙 ID、软件版本）
  /front/item/info           -> 货道商品（cellId / itemId / 价格 / 库存）

依据：pages/index/index.js 的 getDevInfo / getModelConfig / getDevItemInfo 三处调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import NoftelClient

# 设备状态码（pages/index/index.js handleCheckoutDevInfo 的文案映射）
DEVICE_STATUS_TEXT = {0: "离线", 1: "正常", 2: "故障", 5: "禁用"}


@dataclass
class DeviceModel:
    """机型配置。字段名与小程序的 devModelInfo 保持一致，便于对照。"""

    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "")

    @property
    def model(self) -> str:
        return str(self.raw.get("model") or "")

    @property
    def base_model(self) -> str:
        return str(self.raw.get("baseModel") or "")

    @property
    def cell_num(self) -> int:
        return int(self.raw.get("cellNum") or 0)

    @property
    def connect_bluetooth(self) -> bool:
        return bool(self.raw.get("connectBluetooth"))

    @property
    def connect_network(self) -> bool:
        return bool(self.raw.get("connectNetwork"))

    @property
    def has_pwd(self) -> bool:
        return bool(self.raw.get("hasPwd"))

    @property
    def reopen(self) -> bool:
        return bool(self.raw.get("reopen"))


@dataclass
class DeviceInfo:
    """设备归属信息。"""

    raw: dict[str, Any]

    @property
    def dev_id(self) -> str:
        return str(self.raw.get("devId") or "")

    @property
    def status(self) -> int:
        return int(self.raw.get("status") if self.raw.get("status") is not None else -1)

    @property
    def status_text(self) -> str:
        return DEVICE_STATUS_TEXT.get(self.status, f"未知({self.status})")

    @property
    def hotel_id(self) -> str:
        return str(self.raw.get("hotelId") or "")

    @property
    def room_code(self) -> str:
        return str(self.raw.get("roomCode") or "")

    @property
    def hotel_name(self) -> str:
        return str(self.raw.get("hotelName") or "")

    @property
    def bluetooth_id(self) -> str:
        return str(self.raw.get("blueToothId") or "")

    @property
    def soft_version(self) -> str:
        return str(self.raw.get("softVersion") or "")

    def can_purchase(self) -> tuple[bool, str]:
        """复刻客户端前置校验：未绑定 / 非正常状态都不允许下单。"""
        if not self.hotel_id:
            return False, "当前设备未绑定，无法购买"
        if self.status != 1:
            return False, f"当前设备处于{self.status_text}状态，无法购买"
        return True, "ok"


@dataclass
class CellItem:
    """单个货道的商品。"""

    raw: dict[str, Any]

    @property
    def cell_id(self) -> int:
        return int(self.raw.get("cellId") or 0)

    @property
    def item_id(self) -> str:
        return str(self.raw.get("itemId") or "")

    @property
    def item_no(self) -> str:
        return str(self.raw.get("itemNo") or "")

    @property
    def name(self) -> str:
        return str(self.raw.get("itemName") or "")

    @property
    def price(self) -> float:
        return float(self.raw.get("itemPrice") or 0)

    @property
    def stock(self) -> int:
        return int(self.raw.get("itemNum") or 0)

    @property
    def sell_status(self) -> Any:
        return self.raw.get("sellStatus")

    def to_order_item(self, qty: int = 1) -> dict[str, Any]:
        """转成下单 items 元素。

        字段取自 pages/index/index.js 的 orderInfo.items.push：
          itemId / cellId / sellStatus / sellNum / itemNo
        """
        return {
            "itemId": self.item_id,
            "cellId": self.cell_id,
            "sellStatus": self.sell_status,
            "sellNum": qty,
            "itemNo": self.item_no,
        }


def get_model_config(client: NoftelClient) -> DeviceModel:
    result = client.get("/front/dev/getModelConfig", {"devId": client.config.dev_id})
    if not result:
        raise ValueError("该 devId 无机型配置返回")
    return DeviceModel(raw=result[0])


def get_device_info(client: NoftelClient) -> DeviceInfo:
    result = client.get("/front/dev/info", {"devId": client.config.dev_id})
    if not result:
        raise ValueError("设备信息为空")
    return DeviceInfo(raw=result)


def get_cell_items(client: NoftelClient) -> list[CellItem]:
    result = client.get("/front/item/info", {"devId": client.config.dev_id})
    items = (result or {}).get("itemInfo") or []
    return [CellItem(raw=it) for it in items]
