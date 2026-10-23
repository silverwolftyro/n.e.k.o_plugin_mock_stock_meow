# 久留美式收割模拟器（纯虚构 · 反诈沙盘）

> ⚠️ 本项目是一个纯虚构、纯娱乐的反诈教学游戏。所有股票、价格、代币均为虚构，
> 无现金价值，不提供充值、提现、兑换、真实行情对接、投资建议或任何金融服务。
> 项目故意演示“养套杀”和“每日账单斩杀”的收割机制，目的是让玩家在零成本环境中
> 体验被收割的全过程，从而对真实交易保持警惕。
>
> 你连这个游戏都玩不过，就别去真实市场送钱了。
>
> 默认仅限本地/局域网运行。请勿部署为公开金融服务。
> 本项目不承诺长期维护，更新随缘。

## 这是什么

一个跑在 N.E.K.O. 插件系统里的纯虚构反诈沙盘。核心机制是「养套杀」：
早期让你小赢，在你最上头的瞬间精准收割。

- 所有标的 ID 形如 `FAKE-xxx`，**不映射**任何真实股票代码或外汇对
- 价格、费率、代币均为虚构数值，无现金价值
- 代码中**不存在**充值、提现、兑换、入金、出金一类功能，连变量名与占位符都没有
- 灵感来自《FX战士久留美》，但不使用其任何版权内容、角色名与视觉素材

## 当前状态

**P6 完成**。游戏可玩：四个经典档 + 一个无限档，聊天频道里有事件卡片，
插件管理页里有完整的 Hosted TSX 面板（K 线、持仓、账单、猫娘、新闻详情、
结算报告）。体验层已打磨：福利改写成「打工工资」、入场三选一、猫娘的留白、
天台虚惊一场、死亡称号与图鉴、无限模式「跳过本日」。
P6 完成仓库化收口：独立 git 仓库 + CI 工作流、错误码全量走 i18n 查表、
面板按需拉任意标的 K 线、Hosted TSX 通过官方类型检查。

| 分期 | 内容 | 状态 |
| --- | --- | --- |
| P0 | 插件空壳与脚手架 | ✅ 已完成 |
| P1 | `engine/` 纯 Python 引擎 + pytest | ✅ 已完成 |
| P2 | `join`/`buy`/`sell`/`status`/`market` 入口 + 持久化 | ✅ 已完成 |
| P3 | 定时驱动 tick 与状态机（TRAP / SLAUGHTER） | ✅ 已完成 |
| P4 | 无限模式每日循环、三振、高利贷、二道贩子 | ✅ 已完成 |
| P5 | 完整面板 + 聊天卡片 + 猫娘播报与嘲讽 | ✅ 已完成 |
| P5.5 | 体验打磨：打工工资 / 入场三选一 / 猫娘留白 / 天台三屏 / 称号图鉴 / 跳过本日 | ✅ 已完成 |
| P6 | 仓库化收口：独立 git 仓库 / CI 工作流 / 错误码 i18n 查表 / 按需拉 K 线 / 官方 TSX 类型检查 | ✅ 已完成 |

**当前版本 0.7.1**，单测 **452 例全绿**，`neko-plugin check` 0 error，
`check --release` 与 `check --release --market-release` 均通过，
官方 `check-hosted-tsx` 类型检查通过。

## 目录结构

```text
plugin/plugins/juliumei_market/
├── plugin.toml            # 插件清单：身份、入口、能力开关、Hosted UI 声明
├── __init__.py            # 第一层：宿主事件 ↔ 第二层调用 的翻译层
├── cards.py               # P5：9 种事件卡片 + 猫娘嘲讽文案 + 死亡叙事（只产数据，不投递）
├── config.example.toml    # 业务参数模板（不含 [plugin] 段）
├── pyproject.toml         # 零第三方依赖
├── i18n/zh-CN.json        # 本地化文案（错误码 / 面板 / 卡片 / 嘲讽 / 天台三屏）
├── ui/panel.tsx           # Hosted UI 市场面板（P5 完整版 + P5.5 结算叙事与图鉴）
├── engine/                # 第三层：纯标准库游戏引擎
├── modes/                 # 第二层：经典 / 无限 两种模式
├── storage/               # 持久化（DB 权威 + 状态缓存）
└── tests/                 # 452 例单测（含自带 Hosted TSX lint + SDK 导入边界守卫）
```

三层架构规划：

- **第一层（插件层）**：`plugin.toml`、`__init__.py`、`ui/panel.tsx`、`cards.py`、`storage/repo.py`
- **第二层（模式层）**：`modes/classic.py`、`modes/infinite.py`、`modes/shared.py`
- **第三层（引擎层）**：`engine/*.py`，纯标准库，不认识 N.E.K.O 的存在，可独立用 pytest 测试

第一层只做翻译：把宿主事件翻译成对第二层的调用，再把第二层结果翻译成 `Ok` / `Err` 与 UI 数据。
它不直接操作数据库、不直接生成价格或上头指数、不包含任何游戏规则。

## 开发命令

在 N.E.K.O 源码树根目录（`N.E.K.O-main`）执行：

```bash
uv run neko-plugin check "D:\项目\N.E.K.O\neko-猫娘股市\plugin\plugins\juliumei_market"
```

发布预检（**只做校验，不发布**）：

```bash
uv run neko-plugin check --release "D:\项目\N.E.K.O\neko-猫娘股市\plugin\plugins\juliumei_market"
uv run neko-plugin check --release --market-release "D:\项目\N.E.K.O\neko-猫娘股市\plugin\plugins\juliumei_market"
```

> Windows 中文环境提示：CLI 内部有一处未指定 `encoding` 的文件读取，
> 在 GBK 默认编码下会报 `'gbk' codec can't decode byte ...`。
> 加 `PYTHONUTF8=1` 即可绕过（不需要改动源码树）：
> `$env:PYTHONUTF8='1'`

## Hosted TSX 类型检查

面板 `ui/panel.tsx` 已通过官方 `check-hosted-tsx`（TypeScript 5.9 严格模式），
**无类型错误**。该检查需要 `typescript` 依赖，官方脚本会把仓库根算作
N.E.K.O 源码树根，因此本地校验用临时迷你仓库进行（源码树零写入）。

本仓库内的 `tests/test_hosted_tsx.py` 只做自带 lint（不联网、不装依赖）；
类型检查是发布前的独立环节。

## 宿主 SDK 导入边界（**硬红线**）

插件**只准**从公开门面导入宿主 SDK：

| 允许 | 用途 |
|:--|:--|
| `plugin.sdk.plugin` | 插件开发主门面（`Ok` / `Err` / `SdkError` / 装饰器 / `CardSubmissionError` …） |
| `plugin.sdk` | SDK 根命名空间（`EVENT_META_ATTR` 等常量） |
| `plugin.sdk.adapter` | 适配器开发门面 |

**`plugin.sdk.shared.*` 是宿主内部实现，插件一律不许直引** —— 开发机（源码树在
`sys.path` 上）能 import 过去，打包进真实宿主就启动即崩：

```
Plugin 'juliumei_market' lifecycle event 'startup' failed:
Process crashed: No module named 'plugin.sdk.shared.core.cards'
```

创建卡片走**公开 API** `await self.ctx.create_card(html=..., summary=..., css=..., actions=...)`。

`tests/test_sdk_boundary.py` 用 AST 扫描全项目把这条红线固化成回归测试。

## CI

`.github/workflows/verify.yml` 与 `.github/workflows/release.yml` 由官方
`neko-plugin setup-repo juliumei_market --github-actions` 生成，Market 发布走
标准 release 工作流。

仓库行尾统一 LF（`.gitattributes` + `core.autocrlf=false`）。

## 结算页固定文案（不可通过配置关闭）

> 你连这个纯虚构的游戏都玩不过。全球最聪明的钱，正在真实市场里等着你。别去。
>
> 如果你或身边的人正在经历类似情况，可以搜索“国家反诈中心”。

P5 阶段会在结算页附上国家反诈中心官网链接，届时核对官方地址后填入，不使用第三方短链。
