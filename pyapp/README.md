# pyapp —— 业务包

## 用途
协议还原的全部业务代码。按职责四个子包 + 一个编排模块，单向依赖（叶子不反向依赖上层）。

## 模块组成
| 位置 | 职责 | 文档 |
|---|---|---|
| `api/` | HTTP 出口、请求头复刻、设备只读接口 | [api/README.md](api/README.md) |
| `pay/` | 下单、预下单、支付状态轮询 | [pay/README.md](pay/README.md) |
| `ble/` | 蓝牙开柜（分片写入 + notify 重发） | [ble/README.md](ble/README.md) |
| `cli/` | 命令行解析与输出 | 本文件下方 |
| `purchase.py` | 主链路编排与支付闸门 | 本文件下方 |

## 依赖方向
```
cli -> purchase -> {api, pay, ble}
pay  -> api
ble  -> （无业务依赖）
api  -> （无业务依赖）
```
`ble` 与 `pay` 互不 import；两者只在 `purchase.py` 被组合。

## 关键 API
| 名字 | 位置 | 作用 |
|---|---|---|
| `snapshot()` | purchase.py | 一次性拉设备型号/归属/货道（只读） |
| `purchase_and_open()` | purchase.py | 下单 -> 支付 -> 确认 -> 开柜；唯一开柜入口 |

## 副作用
`purchase_and_open` 会真实下订单、触发支付参数生成、并可能**物理开柜**。

## 限制与坑
- 开柜闸门在 `purchase.py`：`getPayStatus` 的 `status ∈ {1,12}` 且 `payCode` 非空。
  不要把这个判断挪到 `ble` 里——那时已经晚了。
- CLI 不提供独立开柜命令，这是项目规则（见根 `CLAUDE.md` 第 2 条）。

## 改动校验点
- 加新子包：同步本表与依赖方向；
- 改 `purchase_and_open` 时序：同步 `docs/接口清单.md` 与 `docs/逆向计划.md`。
