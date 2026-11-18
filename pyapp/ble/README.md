# ble —— 蓝牙开柜

## 用途
把服务端下发的 `payCode` 写进售货机的 BLE 写入特征，触发柜门解锁，并等待设备回包。

## 对外导出
| 名字 | 类型 | 干什么 |
|---|---|---|
| `BleOpener` | class | 扫描 -> 连接 -> 订阅 notify -> 分片写 payCode -> 等回包 |
| `normalize_ble_address` | 函数 | `<BLE_MAC_12HEX>` -> `<BLE_MAC_COLON>` |
| `split_frames` / `hex_to_bytes` | 函数 | 40 字符一片 / 十六进制转字节 |
| `OpenResult` | dataclass | 发送片数、是否收到回包、回包 hex、实际地址 |
| `BleError` | exception | 扫描/连接/写入失败、payCode 为空 |

## 用法示例
```python
from pyapp.ble.opener import BleOpener
op = BleOpener(svc_uuid, notify_uuid, write_uuid)
res = op.open_cell_sync("<BLE_MAC_12HEX>", pay_code)
print(res.notified, res.sent_frames)
```

## 依赖
`bleak`（BlueZ，需本机 hci0 可用）。前置：已拿到服务端签发的 `payCode`。

## 副作用
**会真实打开柜门**。这是本工程唯一改变物理世界的动作。

## 限制与坑
- `pay_code` 为空时直接抛错，不做任何尝试（防误开）。
- 未收到 notify 会重发，最多 3 轮，之后抛「开柜失败1003」，与客户端一致。
- MAC 需在设备旁才扫描得到；虚拟机的蓝牙直通常有问题。
- 不同机型 UUID 可能不同（N 系列为 `0000AE01/AE02`），由 `config/.env` 覆盖。

## 改动校验点
切片长度改动 -> 对照 `utils/util.js::datasubpck`；UUID 改动 -> 同步 `docs/接口清单.md` 附表。
