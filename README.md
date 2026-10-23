# 反向暴富模拟器（纯虚构 · 反诈沙盘）

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

**P7 完成**。游戏可玩：四个经典档 + 一个无限档，插件管理页里有完整的 Hosted TSX 面板
（K 线、持仓、账单、猫娘、新闻详情、结算报告）。体验层已打磨：福利改写成「打工工资」、
入场三选一、猫娘的留白、天台虚惊一场、死亡称号与图鉴、无限模式「跳过本日」。
P6 完成仓库化收口：独立 git 仓库 + CI 工作流、错误码全量走 i18n 查表、
面板按需拉任意标的 K 线、Hosted TSX 通过官方类型检查。
P7 完成社区抽象化与节目效果：全局改名《反向暴富模拟器》（内部 ID `reverse_rich_sim`）、
开源纯净版 / 直播特制版双版本分流、社区名册彩蛋与猫娘弹幕。
**P7.1（0.8.1）**把 0.8.0 在 Steam 宿主上把 UI 搞崩的那批视觉特效**彻底回滚**
（病危闪烁 / 裂纹伪元素 / 半透明滤镜 / 按钮漂移），面板回到宿主组件的默认样式。

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
| P7 | 社区抽象化与节目效果：改名 `reverse_rich_sim` / 双版本（导演模式开关）/ 社区名册 + 猫娘弹幕 / 荒诞弹窗 / Emoji 海 | ✅ 已完成 |
| P7.1 | **回滚 0.8.0 崩 UI 的视觉特效**：病危只留顶部红色 Alert + 数字染红；去掉闪烁 / 裂纹 / 滤镜 / 遮罩 / 按钮漂移；面板回宿主默认样式 + 防御性 CSS | ✅ 已完成 |

**当前版本 0.8.1**，单测 **498 例全绿**，`neko-plugin check` 0 error，
`check --release` 与 `check --release --market-release` 均通过，
官方 `check-hosted-tsx` 类型检查通过。

## 目录结构

```text
plugin/plugins/reverse_rich_sim/
├── plugin.toml            # 插件清单：身份、入口、能力开关、Hosted UI 声明
├── __init__.py            # 第一层：宿主事件 ↔ 第二层调用 的翻译层
├── cards.py               # P5：9 种事件卡片 + 猫娘嘲讽文案 + 死亡叙事（只产数据，不投递）
├── config.example.toml    # 业务参数模板（不含 [plugin] 段）
├── pyproject.toml         # 零第三方依赖
├── i18n/zh-CN.json        # 本地化文案（错误码 / 面板 / 卡片 / 嘲讽 / 天台三屏 / 视觉降级 / 导演台）
├── community_roster.json  # P7：社区名册彩蛋（群友人设 + 方向中立的抽象新闻池）
├── ui/panel.tsx           # Hosted UI 市场面板（P5 完整版 + P5.5 结算叙事与图鉴 + P7 节目效果）
├── engine/                # 第三层：纯标准库游戏引擎
├── modes/                 # 第二层：经典 / 无限 两种模式
├── storage/               # 持久化（DB 权威 + 状态缓存）
└── tests/                 # 492 例单测（含自带 Hosted TSX lint + SDK 导入边界守卫 + 数据库懒建表守卫 + 运行时能力对齐守卫 + P7 效果守卫）
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
uv run neko-plugin check "D:\项目\N.E.K.O\neko-猫娘股市\plugin\plugins\reverse_rich_sim"
```

发布预检（**只做校验，不发布**）：

```bash
uv run neko-plugin check --release "D:\项目\N.E.K.O\neko-猫娘股市\plugin\plugins\reverse_rich_sim"
uv run neko-plugin check --release --market-release "D:\项目\N.E.K.O\neko-猫娘股市\plugin\plugins\reverse_rich_sim"
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

## P7：双版本与节目效果

### 两个版本，一个开关

同一份代码，两种玩法，由 `config.example.toml` 里的 `[director] enabled` 决定：

| 开关 | 版本 | 行为 |
|:--|:--|:--|
| `enabled = false`（**默认**） | 开源纯净版 | 严格遵守原有数学概率，**无任何后台干预**；`director` 入口恒返回 `error.director_disabled` |
| `enabled = true` | 直播特制版 | 打开导演台，可安排暴跌 / 强制放贷 / 强制断供 / 立即斩杀 |

导演指令全部**复用引擎既有函数**（`apply_jump` / `apply_death` / 引擎的
借贷参数与断供斩杀线），`engine/` 与 `modes/` **一行都没改**。
暴跌是**排队制**：面板点一下排一条，**下一个 tick** 在 `advance_market` 之后、
模式层之前施加，让集中度、收割等后续反应照常发生。

### 赛博精神污染 UI（全部落在 `ui/panel.tsx`）

宿主 0.9.0 **没有任何卡片 API**（`create_card` / `ChatCard` / `PluginView` /
`html_card` 在冻结二进制里各 0 次命中），所以面板里能做的表现就这些：

1. **病危** —— 现金 < 0 / 濒死时：面板**最顶部一条红色 `Alert` 警告条** +
   「资金」那个关键数字**染红**（`var(--danger)`）。
   > 0.8.1 起**只保留这两种**。原来的红色高频闪烁（改祖先 `opacity`）、
   > 边框裂纹伪元素（`inset:0` + 对角渐变）、半透明灰化滤镜（`filter:`）
   > 已在 0.8.1 **全部回滚** —— 它们在真实宿主里把整个面板搞崩了（见下文）。
2. **荒诞弹窗** —— `Modal` 版「赛博网贷广告」，`closeOnBackdrop` 与
   `closeOnEscape` 都关掉，只能选「借走 500 万」或「强制破产」
3. **Emoji 海结算** —— 终局 `Grid` 铺满 60 个 🤡 / 🪦（纯内容，无背景无定位）
4. ~~**按钮漂移**~~ —— **0.8.1 已回滚**（自定义包裹会破坏宿主按钮组布局），
   「梭哈 / 借高利贷」一律用宿主原生 `ActionButton`

### ⚠️ 0.8.0 的真机 UI 事故与 0.8.1 的回滚

0.8.0 在 Steam 宿主（0.9.0）上把面板 UI **整个搞崩**：巨大的交叉 X 线条盖住表格、
整体泛白半透明、表格按钮错位挤成一团。根因四条：

| # | 写法 | 后果 |
|:--|:--|:--|
| 1 | `.neko-card::after { position:absolute; inset:0; background: 两条对角 linear-gradient }` | 面板上出现**巨大的 X 线条** |
| 2 | 祖先容器 `animation` 改 `opacity`（0.30 / 0.45） | 整个面板**泛白半透明**，文字全看不清 |
| 3 | 祖先 `filter: grayscale()` | filter 给祖先建 containing block ⇒ **布局错位** |
| 4 | `<div className="rrm-stage-*">` 包裹层 | 卡片不再是 `Stack` 的**直接** flex item ⇒ **间距消失、挤成一团** |

0.8.1 的硬规矩（已被 `tests/test_p7_effects.py` 的源码扫描守卫锁死）：

* 面板**只用宿主组件的默认样式** —— 不碰 `opacity` / `filter` / `animation` /
  `position` / 背景 / 伪元素；
* 卡片必须是 `Stack` 的**直接** flex item（和 P6 已验证的骨架一致）；
* 自定义 CSS 只剩「盒模型 + 溢出收敛」这类**不改变布局**的声明；
* 病危只有顶部红条 + 数字染红两种表现。

### 社区名册与猫娘弹幕

`community_roster.json` 放群友人设与**方向中立**的抽象新闻池（如「WHS 连喝三罐
黑色易拉罐，宣布进军元宇宙」）。名册新闻**只换标题，正文一字不动** —— 正文是
引擎的方向信号，动它就破坏 P4 §11 反泄露铁律。

猫娘实时嘴臭走面板里的 `Alert` 条：买入 / 卖出 / 跳过本日 / 放贷 / 破产时触发。
（0.8.0 原本是 `position: sticky` 的悬浮窗，0.8.1 改成文档流里的 `Alert` ——
sticky 会给祖先建滚动容器，是布局事故的帮凶之一。）

### 家道中落的档位（只留文案，不留视觉特效）

| 档位 | 触发 | 表现（0.8.1） |
|:--|:--|:--|
| 富二代 | 一、二关 | 「资金」，默认样式 |
| 断供 | 第三关 | 「生活费」，默认样式 |
| 濒死 | 无限模式 | 「下顿饭的钱」，默认样式；病危时顶部红条 + 数字染红 |

> 0.8.0 的暗金边框 / 灰化滤镜 / 裂纹伪元素 / 心电图暗红 K 线**已全部回滚**。
> 档位仍然存在，但只通过**文案**表达 —— 这是 P4 §11 反泄露铁律允许的范围内
> 唯一的「视觉降级」。

阈值全部可配（`[visual]` 段的 `broke_day_threshold` / `dying_assets_threshold` /
`panic_distance` / `emoji_sea_size`）。

## 宿主 SDK 导入边界（**硬红线**）

插件**只准**从公开门面导入宿主 SDK：

| 允许 | 用途 |
|:--|:--|
| `plugin.sdk.plugin` | 插件开发主门面（`Ok` / `Err` / `SdkError` / 装饰器 …） |
| `plugin.sdk` | SDK 根命名空间（`EVENT_META_ATTR` 等常量） |
| `plugin.sdk.adapter` | 适配器开发门面 |

**`plugin.sdk.shared.*` 是宿主内部实现，插件一律不许直引** —— 开发机（源码树在
`sys.path` 上）能 import 过去，打包进真实宿主就启动即崩：

```
Plugin 'reverse_rich_sim' lifecycle event 'startup' failed:
Process crashed: No module named 'plugin.sdk.shared.core.cards'
```

创建卡片走**公开 API** `await self.ctx.create_card(html=..., summary=..., css=..., actions=...)`
—— 但必须**按能力探测**地用（见下文「宿主可能压根没有卡片 API」）。

### 门面导出的符号集随宿主版本而变 ⚠️

**源码树里有，运行时未必有。** 已踩过三次：

| 版本 | 症状 | 根因 |
|:--|:--|:--|
| 0.7.0 | `No module named 'plugin.sdk.shared.core.cards'` | 直引宿主**内部实现** `plugin.sdk.shared.*` |
| 0.7.1 | `cannot import name 'CardSubmissionError' from 'plugin.sdk.plugin'` | 门面在 Steam 版运行时里**没导出**这个类 |
| 0.7.4 | `'SdkContext' object has no attribute 'create_card'` | 宿主 0.9.0 的 ctx **没有**卡片方法（硬调就炸） |

因此：

1. **只 import 运行时验证存在的名字**（`tests/test_sdk_boundary.py` 里的
   `RUNTIME_FACADE_NAMES` 白名单，依据是 Steam 版宿主实测）。
2. **不要为具体的宿主异常类写 `except`** —— 一律 `except Exception` 兜底记日志。
3. 门面上 `cannot import name` ⇒ **版本不匹配**，改用最保守的写法，不要死磕。
4. **方法级 API 也要探测**：`create_card` 在 Steam 版宿主（0.9.0）的
   `SdkContext` 上**根本不存在**（在冻结的 `projectneko_server.exe` 里扫过：
   `create_card` / `ChatCard` / `PluginView` / `html_card` 全部 **0 次命中**）。
   所以卡片走 `getattr(self.ctx, "create_card", None)` 探测：没有就降级成
   一条纯文本聊天消息（`push_message`，0.9.0 里是有的）。

`tests/test_sdk_boundary.py` 用 AST 扫描全项目把这条红线固化成回归测试。

### 宿主可能在插件实例构造**之后**才下发有效配置

Steam 版宿主（0.9.0）实测：插件进程启动时 `ctx._effective_config` 里**压根没有**
`[plugin.database]` / `[plugin_state]` 段 → `self.db.enabled` 是 `False` →
`on_startup` 建表失败。宿主补发配置后 DB 能开会话了，但**表从未建成**
（0 字节的 `.db`），之后每次查询都撞 `no such table`，插件永远卡在
"数据库不可用"。

**更致命的是 0.7.4 才定位到的第二层**：即使宿主后来补发了配置，
`refresh_runtime_config()` **不带参数**时只会重新解析插件进程里那份
**缺能力段**的缓存 —— 于是"刷新"等于什么都没做，`db.enabled` 永远是
`False`，**1 秒心跳每秒钟都撞在"数据库不可用"上，tick 永远不推进**
（真机实测：手动调 `market_tick` 入口能推进 tick，说明 tick 流水线本身没坏，
坏的是心跳读不到库）。

修法（插件侧，不依赖宿主版本）：

* schema 改成**懒建表**（`Repo.ensure_schema`）——第一次真正用数据库时补建，
  失败也不放弃；`tests/test_db_bootstrap.py` 锁死这条性质。
* **运行时能力对齐**（`ReverseRichSimPlugin._effective_runtime_config`）：
  把**宿主缓存 > 读到的配置 > 插件自己的 `plugin.toml` 清单**合成一份完整
  配置再喂给 `refresh_runtime_config`。清单兜底的合法性来自 SDK 自己的话：
  *"plugin.toml manifest metadata is authoritative"*；宿主**显式**给过的值
  仍然优先（只在**缺段**时补，不覆盖）。
* 降级路径会**主动拉一次最新有效配置**再重试；心跳读不到库时**刷新 + 重试**
  一次，并且**不重置时间基线**（否则库恢复后时间差被抹平，市场永远追不上）。
* `tests/test_runtime_config.py` 锁死这组性质（8 例）。

## CI

`.github/workflows/verify.yml` 与 `.github/workflows/release.yml` 由官方
`neko-plugin setup-repo reverse_rich_sim --github-actions` 生成，Market 发布走
标准 release 工作流。

仓库行尾统一 LF（`.gitattributes` + `core.autocrlf=false`）。

## 结算页固定文案（不可通过配置关闭）

> 你连这个纯虚构的游戏都玩不过。全球最聪明的钱，正在真实市场里等着你。别去。
>
> 如果你或身边的人正在经历类似情况，可以搜索“国家反诈中心”。

P5 阶段会在结算页附上国家反诈中心官网链接，届时核对官方地址后填入，不使用第三方短链。
