# cli —— 命令行与交互向导

## 用途
两个入口共用同一套编排与门禁：`wizard.py` 供人工操作，`__main__.py` 供脚本与复现。

## 对外导出
| 名字 | 类型 | 干什么 |
|---|---|---|
| `interactive_main` | 函数 | 交互式向导主入口，返回退出码 |
| `Cart` | dataclass | 购物车 `{cellId: 数量}`，带增删改与合计 |
| `UserAbort` | exception | 用户中断（EOF / Ctrl-C） |
| `main` | 函数 | 参数式命令入口（`info` / `cells` / `pay-status` / `buy`） |

## 用法示例
```python
from pyapp.cli.wizard import interactive_main
interactive_main(client)                      # 真实终端
interactive_main(client, input_fn=fake, out=log.append, snap=snap)   # 测试
```

## 依赖
`pyapp.purchase`（编排）、`pyapp.api.device`（数据结构）。不直接发 HTTP。

## 副作用
向导在用户输入 `yes` 后才会下单；`input_fn`/`out` 可注入，便于测试时完全隔离 IO。

## 限制与坑
- `snap` 参数可注入快照，测试靠它跳过网络；生产调用不传。
- 商品选择语义是**列表序号**（1 起），不是货道号；序号越界时才回退按货道号匹配。
- 售罄商品在加入阶段就被拦下，不进入购物车。
- 中断返回码 130，取消返回 0，被闸门拒绝返回 2，业务失败返回 1。

## 改动校验点
- 改购物车逻辑 -> 同步 `tests/test_wizard.py`；
- 改门禁 -> 同时改 `pyapp/api/client.py` 与根 `CLAUDE.md` 第 3 条。
