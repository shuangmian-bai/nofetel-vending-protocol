# 项目规则（诺丰泰售货机协议还原）

本文件只写**本项目特有**的规则；全局与类型规则指向 `<SKILLS>/project-conventions`。

## 继承规范

| 层 | 位置 | 适用范围 |
|---|---|---|
| 全局规则 | `<SKILLS>/project-conventions/references/01-全局规则.md` | 全部 |
| 类型分册 | `<SKILLS>/project-conventions/references/03-类型-python-爬虫与协议逆向.md` | 本项目属此类 |
| 逆向方法论 | `<SKILLS>/reverse-analysis/subskills/miniprogram/SKILL.md` | 协议怎么破 |
| 凭据纪律 | `<SKILLS>/secret-broker/SKILL.md` | openId / sessionKey |

## 项目定位

还原微信小程序 `wxd005d05c066d1e3e` 的「下单 → 支付 → 开柜」链路，产出可单独验证每步的 CLI。
**不是批量采集工具**，不提供并发扫描、货道枚举、跨设备遍历。

## 项目特定规则

### 1. 凭据边界（覆盖全局第 3 条，更严格）

- `openId` / `sessionKey` / 任何 `payCode` **一律不进代码、不进 `.env`、不进日志、不进文档**；
- 运行时通过环境变量 `VAULT_NOFETEL_OPENID` 注入（可用任意密钥库工具，见 README）。
- 日志里出现 `open_id` 的字段必须脱敏：`NoftelClient.open_id` 已设 `repr=False`；
  不要新增任何会打印它的 `logger.info`。
- 抓到的设备内存 / 报文里若含 openId，写入密钥库后**立即 shred 明文副本**。

### 2. 配置分级（`.env` 只放非敏感项）

- `config/.env` 允许出现：设备标识 `NOFETEL_DEVID`、客户端常量、门禁阈值、轮询与 BLE 参数。
- `config/.env` **禁止出现**：`openId`、`sessionKey`、`payCode`、任何 token。
  原因：无 POSIX 权限位的磁盘（NTFS/exFAT）上 `chmod 600` 不生效（实测恒为 777），
  文件对本机所有用户可读；凭据一旦写入就等于公开。
- 新增配置项先判断级别，写进 `docs/配置与凭据.md` 再写进 `config/.env.example`。
- 设备 ID 改动只走 `NOFETEL_DEVID`（`.env` / 环境变量 / `--dev-id`），
  **不允许**在代码里给 `dev_id` 任何默认值。

### 3. 写操作门禁（本项目最重要的约束）

| 操作 | 门禁 |
|---|---|
| `/front/order/addOrder` | `confirm=True` + 金额 ≤ `NOFETEL_MAX_ORDER_AMOUNT` |
| `/front/smallWxpay/unifiedOrder` | `confirm=True` |
| 蓝牙开柜 | 必须 `getPayStatus.status ∈ {1, 12}` 且 `payCode` 非空 |

- **禁止提供"单独开柜"的命令行入口**，开柜只能由 `purchase_and_open` 在支付确认后触发。
- 新增任何写接口，先加进 `NoftelClient.post` 的门禁，再写业务调用。
- 金额闸门默认 50 元；调高必须在本文件写明理由与日期。
- **交互向导与参数式命令必须共用同一编排链路**（`pyapp.purchase`）。
  向导里的"输入 yes"只是参数式 `--confirm` 的交互形态，不得成为更宽松的旁路；
  新增入口时同样受本条约束。向导改动必须同步 `tests/test_wizard.py`。

### 4. 设备副作用

- 本工具唯一会改变物理世界的动作是**开柜**（柜门弹开、商品可被取走）。
  任何探测式调用都不许触发它；测试开柜前必须确认人在设备旁，并已在回复里说明。
- 记录 `data/samples/` 里的样本时，一律去掉 openId 与 payCode 的真实值。

### 5. 接口变更追踪

- 服务端无版本号、无签名，改版很难从错误码看出。因此 `docs/接口清单.md` 每条必须写
  「实测日期」；超过 30 天未复验的接口，使用前先跑一次只读验证。

### 6. 文件规模与结构

- 单文件 ≤ 500 行（全局第 11 条）。`pyapp/api/`、`pyapp/pay/`、`pyapp/ble/` 各自单一职责，
  不互相 import 业务逻辑，编排只在 `pyapp/purchase.py`。

## 与 README 的分工

- `README.md`：怎么装、怎么跑、架构图、坑。
- 本文件：不能违反的规则与边界。
- `docs/`：协议事实与还原过程（可随证据更新的知识）。

## 有意偏离

| 条目 | 偏离内容 | 理由 |
|---|---|---|
| 全局第 23 条（界面声明式资源） | 本项目是 CLI，无图形界面 | 无 GUI，规则不适用 |
