# 小程序协议逆向样本：售货机「下单 → 支付 → 开柜」链路还原

一个**协议逆向方法论样本工程**。以微信小程序 `wxd005d05c066d1e3e` 与一台 N300-4 售货机为对象，
把「扫码 → 取设备信息 → 下单 → 支付 → 确认已支付 → 蓝牙开柜」整条链路，
从 `.wxapkg` 解包一路还原到可运行的 Python 代码与可复核的文档。

它演示的是**怎么做**：如何从混淆包里定位接口、如何判定有无加密、如何设计写操作门禁、
如何把物理不可逆动作关进「前置条件已验证」的唯一入口。**不是**开箱即用的商品工具。

> 所有设备标识、酒店与房间信息、蓝牙地址、凭据均已脱敏为占位符。
> 用途边界与法律责任见 [DISCLAIMER.md](DISCLAIMER.md)。

## 这个仓库能学到什么

| 主题 | 落点 |
|---|---|
| 小程序解包到接口清单的路径 | `docs/情报.md`、`docs/接口清单.md` |
| 如何判定「有没有加密参数」并留下证据 | `docs/加密说明.md` |
| 写操作门禁：confirm + 金额阈值 + 物理副作用隔离 | `pyapp/api/client.py`、`pyapp/purchase.py` |
| 按副作用可逆性而非 HTTP 方法做权限分层 | `CLAUDE.md` 第 3 条 |
| 配置与凭据分离（`.env` 放什么、密钥库放什么） | `docs/配置与凭据.md` |
| BLE 帧分片、notify 重发、MAC 地址规整 | `pyapp/ble/opener.py` |
| 交互式向导如何复用同一套门禁 | `pyapp/cli/wizard.py` |

## 定位

| 项 | 说明 |
|---|---|
| 目标 | 微信小程序 `wxd005d05c066d1e3e`，落地域名 `c.youzhuyougou.com`，业务后端 `1.nofetel.com` |
| 设备 | `<DEVICE_ID>`（N300-4 / base T300，4 货道，离线机型，仅蓝牙开柜） |
| 鉴权 | 仅 `app-id` + `user-id`(openId) + `version` 三个请求头，**服务端无签名** |
| 开柜 | 蓝牙（BLE）写 `payCode`；`payCode` 只能由服务端在支付成功后下发 |
| 产出 | 可运行的 CLI（只读 / 下单 / 支付轮询 / 蓝牙开柜）+ 协议文档 |

## 环境与运行

依赖通过项目内 venv 隔离，不污染系统 Python：

```bash
git clone https://github.com/shuangmian-bai/nofetel-vending-protocol.git
cd nofetel-vending-protocol

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

需要 Python 3.11+ 与 BlueZ（蓝牙开柜依赖 `bleak`，只读功能不需要蓝牙）。

凭据**不落盘、不进 `.env`**，运行时通过环境变量 `VAULT_NOFETEL_OPENID` 注入：

```bash
# 任何能设环境变量的方式都行，例如：
read -rs VAULT_NOFETEL_OPENID        # 交互式输入，不回显
export VAULT_NOFETEL_OPENID
.venv/bin/python main.py info
```

本工程在作者环境下使用密钥库工具注入（值只进子进程环境、不进 argv 与日志）：

```bash
vault use nofetel_openid -- .venv/bin/python main.py info
```

没有该类工具时，务必确认 `.env` 所在磁盘的 `chmod 600` 真实生效再考虑落盘，
否则就用上面的环境变量方式。判定方法与分级见 `docs/配置与凭据.md`。

配置写在 `config/.env`（由 `config/.env.example` 复制），**只放非敏感参数**：
设备标识、客户端常量、门禁阈值、BLE 特征。

设备 ID 三种注入方式，优先级 **命令行 > 环境变量 > `.env`**：

```bash
.venv/bin/python main.py info                            # 用 config/.env 里的值
NOFETEL_DEVID=<DEVICE_ID> .venv/bin/python main.py info   # 环境变量覆盖
.venv/bin/python main.py --dev-id <DEVICE_ID> info        # 命令行优先
```

`openId` / `sessionKey` **不进 `.env`**：在无 POSIX 权限位的磁盘（NTFS/exFAT）上
`chmod 600` 是空操作，文件对该机所有用户可读。判定与分级见 `docs/配置与凭据.md`。

## 两种使用方式

### 一、交互式（推荐，人工操作用这个）

不带子命令直接运行，进入购物向导：

```bash
.venv/bin/python main.py            # 或显式写 main.py interactive
```

向导流程（全程用序号选择，不需要记货道号与参数）：

```
[1] 商品列表  [2] 加入购物车  [3] 改数量  [4] 移除  [5] 查看购物车  [6] 结算  [0] 退出
```

- 商品列表会标注库存，售罄商品无法加入；
- 一次可加多件（输入 `1,3` 或 `1 3`）；
- 加购时会校验库存，超出直接提示"库存只有 N"；
- 结算页展示明细与合计，**必须输入 `yes`** 才会创建订单；
- 创建后自动进入支付轮询，支付成功且拿到服务端 `payCode` 才触发蓝牙开柜。

与参数式命令共用同一条编排链路，门禁完全一致，不存在"用向导就能绕过闸门"的路径。

### 二、参数式（脚本与复现用）

| 命令 | 性质 | 说明 |
|---|---|---|
| `info` | 只读 | 设备型号、归属酒店/房间、状态、是否可购买 |
| `cells` | 只读 | 货道商品、单价、库存 |
| `pay-status --order-id X` | 只读 | 查询订单支付状态（是否已支付、是否已下发 payCode） |
| `buy --cell N --confirm [--no-open]` | **写** | 单件下单 → 预下单 → 轮询支付 → 确认已支付 → 蓝牙开柜 |

写操作有两道门禁：`--confirm` 显式确认（向导里是输入 `yes`），以及
`NOFETEL_MAX_ORDER_AMOUNT` 金额上限。任一不满足直接拒绝，且**不会发出任何订单请求**。

## 测试

向导的购物车算术、售罄拦截、库存校验、金额闸门与取消路径均有离线单测（不触网、不下单）：

```bash
.venv/bin/python tests/test_wizard.py      # 或用 pytest tests/ -q
```

## 架构

```
main.py                 唯一入口（转接 CLI）
pyapp/
├── api/client.py       唯一 HTTP 出口：请求头复刻 + 凭据注入 + 写操作门禁
├── api/device.py       设备型号 / 归属 / 货道（只读）
├── pay/order.py        下单 / 预下单 / 支付状态轮询（写 + 读）
├── ble/opener.py       蓝牙开柜：帧切片 + notify 订阅 + 重发
├── purchase.py         主链路编排与支付闸门（支持购物车）
├── cli/wizard.py       交互式向导：商品 -> 购物车 -> 结算 -> 支付 -> 开柜
└── cli/__main__.py     参数式命令与入口分发
config/.env.example     非敏感配置模板
docs/                   情报、接口清单、加密/鉴权说明、逆向计划
data/samples/           协议样本（还原依据，只增不删）
```

## 约定与坑

- **openId 是唯一身份**：服务端只认 `user-id` 头，拿到 openId 即可读写该用户全部数据；
  因此 openId 必须存密钥库，日志与文档一律脱敏。
- **payCode 不可自造**：客户端不计算开柜指令，只透传服务端下发的十六进制串。
  任何"本地生成 payCode"的做法都是错的。
- **该设备离线**（`connectNetwork=0`），远程开柜不可用，只能现场蓝牙；
  `blueToothId` 需从 `<BLE_MAC_12HEX>` 规整成 `<BLE_MAC_COLON>`。
- **响应结构**：HTTP 恒 200，业务错误体现在 `code != 200`（客户端也只看 `code`）。
- **版本号双轨**：`utils/http.js` 为 `V3.8.0-20250613`，`utils/api.js` 为 `V3.7.7-20240717`；
  本工具统一用前者，服务端两者都接受。

## 文档索引

| 文件 | 内容 |
|---|---|
| [docs/情报.md](docs/情报.md) | 目标情报：站点形态、鉴权、设备样本、风险特征 |
| [docs/配置与凭据.md](docs/配置与凭据.md) | 什么进 .env、什么必须进密钥库、设备 ID 注入优先级 |
| [docs/接口清单.md](docs/接口清单.md) | 逐接口登记（路径/方法/参数/返回/依据） |
| [docs/加密说明.md](docs/加密说明.md) | 为何"无加密"、鉴权强度、需要实证的结论 |
| [docs/逆向计划.md](docs/逆向计划.md) | 阶段划分与准入门槛、剩余待验证项 |
