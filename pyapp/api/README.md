# api —— 接口层

## 用途
复刻小程序 `utils/api.js` 的请求约定：统一请求头、openId 注入、写操作门禁、响应解包。

## 对外导出
| 名字 | 类型 | 干什么 |
|---|---|---|
| `NoftelConfig` | dataclass | 从环境变量加载运行参数（不含凭据值） |
| `NoftelClient` | class | 唯一 HTTP 出口；`get()` 只读，`post()` 带 confirm 与金额闸门 |
| `ApiError` / `ConfirmRequired` | exception | 业务失败 / 缺确认 |
| `get_model_config` `get_device_info` `get_cell_items` | 函数 | 三个只读设备接口 |
| `DeviceModel` `DeviceInfo` `CellItem` | dataclass | 上面三个接口的结构化视图 |

## 用法示例
```python
from pyapp.api.client import NoftelConfig, NoftelClient
from pyapp.api.device import get_device_info

with NoftelClient(NoftelConfig.from_env()) as c:
    print(get_device_info(c).room_code)
```

## 依赖
`httpx`。openId 来自环境变量 `NOFETEL_OPENID` 或 `config/.env`（环境变量优先）。

## 副作用
无。`__post_init__` 建一个 `httpx.Client`，`close()` 释放。

## 限制与坑
- 服务端 HTTP 恒 200，业务失败看 `code`；`_parse` 已统一处理。
- `post()` 默认 `confirm=False`，会抛 `ConfirmRequired`——这是故意的。
- `open_id` 设了 `repr=False`，不要把对象直接塞进日志。

## 改动校验点
改请求头 -> 同步 `docs/接口清单.md` 的通用约定；新增写接口 -> 先过 `post()` 门禁。
