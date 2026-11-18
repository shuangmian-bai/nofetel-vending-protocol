# pay —— 下单与支付状态

## 用途
还原 `/front/order/addOrder` -> `/front/smallWxpay/unifiedOrder` -> `/front/smallWxpay/getPayStatus`
的时序，产出订单号与开柜指令 `payCode`。

## 对外导出
| 名字 | 类型 | 干什么 |
|---|---|---|
| `create_order` | 函数 | 下单（写，需 confirm） |
| `unified_order` | 函数 | 取微信支付参数（写，需 confirm） |
| `get_pay_status` | 函数 | 单次查支付状态（只读） |
| `wait_for_payment` | 函数 | 轮询到支付成功，返回含 payCode 的结果 |
| `calculate_amount` | 函数 | 按货道价格算合计，供金额闸门 |
| `PAID_STATUS` / `BLUETOOTH_LOST` | 常量 | `(1, 12)` / `5001` |

## 用法示例
```python
order = create_order(client, items, amount=39.0, confirm=True)
params = unified_order(client, order.order_id, confirm=True)   # 交给 wx.requestPayment
pay = wait_for_payment(client, order.order_id)                 # 阻塞到已支付
assert pay.pay_code
```

## 依赖
`pyapp.api.client`。不含蓝牙逻辑。

## 副作用
`wait_for_payment` 会阻塞（最长 `pay_poll_max * pay_poll_interval`，默认 150 秒）。

## 限制与坑
- `payCode` 只有支付成功后才有；未支付时为空字符串。
- 频控时 `addOrder` 不返回 orderId，`create_order` 直接抛 `ApiError`。
- status 的其他值（非 1/12/5001）都按「未支付，继续轮询」处理，与客户端一致。

## 改动校验点
轮询参数改动 -> 同步 `docs/接口清单.md` 第 6 条；`PAID_STATUS` 改动必须有真实支付样本支撑。
