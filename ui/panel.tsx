// 拟股喵喵 · 市场面板（P8 清丽重构版）
//
// Hosted TSX 规则（.agent/skills/neko-plugin/references/hosted-ui-authoring.md）：
//   * 只从 `@neko/plugin-ui` 导入；不引 npm 包；不引外部裸模块；
//   * **禁**动态 `import()`、**禁** class 组件、**禁** Context / portals /
//     Suspense / dangerouslySetInnerHTML；
//   * 相对依赖必须留在插件根目录内，且 ≤32 文件 / ≤512 KiB（本面板是单文件）；
//   * 运行时是**轻量 React-like 渲染器**（不是 React），JSX 工厂是 `h`；
//   * iframe sandbox = allow-scripts，不要依赖普通页面全局。
//
// 三条**渲染器限制**直接决定了本文件的写法：
//   1. 宿主用 `document.createElement()` 建元素 → **画不了真 SVG**，
//      所以 K 线是用 div 拼出来的（见 `Kline`），小折线用 `<canvas>` 画
//      （宿主渲染器支持 `canvas`，且 `ref` 会挂到真实 DOM 节点上）。
//   2. `CommonProps` 里**没有** `style` → 需要内联样式的地方直接用
//      导出的 `h()`（它的 props 是 `any`）。
//   3. `style` 传的是**对象**（运行时逐键写 `dom.style[key]`），
//      **不是** CSS 字符串；CSS 自定义属性（`--x`）用 `setProperty` 也支持。
//
// 0.8.1 起的**硬规矩**（Steam 宿主 0.9.0 上 0.8.0 把面板 UI 整个搞崩了）。
// P8.0 把视觉整个翻面成「清丽轻科技风」，但下面这些**崩溃防线一条都没破**：
//   * 零 ``position``（absolute / fixed）、零 ``inset``、零 ``z-index``；
//   * 零 ``filter``（**真毛玻璃做不了** —— 它靠 ``backdrop-filter``，属于
//     filter 家族，会给祖先建立 containing block 把宿主布局搞歪；这里用
//     「半透明白 + 淡阴影 + 16px 圆角」做质感近似）；
//   * 零 ``opacity``（半透明一律用 ``rgba`` 表达）；
//   * 零伪元素、零 ``@keyframes``；唯一的 ``animation`` 是 ``animation: none``；
//   * 渐变**只允许纵向**（180deg）—— 0.8.0 的巨大 X 线条是
//     ``position:absolute + inset:0 + 对角渐变`` 做的，这里既没有定位也没有
//     对角渐变；
//   * 卡片与布局容器之间**不许有自定义 div 包裹层**（卡片必须是布局容器的
//     **直接**子元素）—— 0.8.0 的第四条根因；
//   * 「病危」只有两种表现：面板顶部一条红色 `Alert` + 关键数字染红。
//
// P7.2 的**极简重构**（先做减法，再塞抽象文案）：
//   * K 线搬到**面板最上方**，占满全宽，当主视觉；
//   * 行情表删掉成本价 / 可卖 / 占比 / 标签，只留「名称 + 走势小折线 +
//     现价 + 超大红绿涨跌」；
//   * 持仓表删掉数量 / 均价 / 可卖 / 占比 / 标签，只留「名称 + 当前市值 +
//     大号红绿盈亏」；
//   * 操作区改成**一键梭哈 / 一键割肉**（输入框、下拉框全部收掉）；
//   * 猫娘弹幕变成**常驻在面板底部**的固定框（文档流，不悬浮）。
//
// P8.0 的**清丽轻科技风**（详见 RRM_CSS 的注释）：
//   * 主题靠**重新定义宿主 CSS 变量**换皮 —— 宿主所有组件（卡片 / 按钮 /
//     徽标 / 文本）一次性变成浅色系，不用逐个组件覆写；
//   * 行情表换成**方块卡片网格**（宿主 `Columns` 的 fluid 模式，窗口宽就是
//     4 列、窄就是 2 列）；
//   * 操作台换成**两个并排的胶囊大按钮**（各占一半宽）；
//   * 群友（社区名册）在名字旁边挂一个**小圆头像**（`ui/assets/` 的内联 PNG，
//     加载失败退回专属 Emoji，绝不破图）。
//
// 局中隐藏（P4 §14 / P5 方案 B）：本面板**不显示** heat、反转日、新闻真假。
// 载荷里没有的东西，面板也拿不到 —— 面板只渲染 `state` 里的白名单字段。
import {
  ActionButton,
  Alert,
  Button,
  Card,
  Columns,
  Divider,
  Grid,
  Inline,
  KeyValue,
  List,
  Modal,
  Page,
  Select,
  Stack,
  StatusBadge,
  Text,
  h,
  showToast,
  useEffect,
  useLocalState,
  useMemo,
  useRef,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps, Tone } from "@neko/plugin-ui"

import { AVATARS, MEOW_BANKRUPT, MEOW_LOAN } from "./assets/avatars"

type KlinePoint = [number, number]

/**
 * 宿主传进来的翻译函数（``PluginSurfaceProps["t"]``）。
 *
 * P8.0：方块卡片是**独立组件**（不 Panel 的闭包里），所以要显式把
 * ``t`` 传下去 —— **绝不在组件里写裸中文字面量**（P8 红线：所有新增
 * 文字必须进 i18n 词典）。
 */
type Translate = (key: string, params?: Record<string, any>) => string

/**
 * 把后端的**内部枚举**翻译成界面文案。
 *
 * P8.1 事故：结算页把 ``starved`` / ``quick`` 这种英文枚举直接怼在玩家脸上，
 * 连 ``{mode}`` 占位符都原样漏了出来。这里统一走 i18n 词典，
 * **翻不到就显示破折号** —— 宁可空着，也不能让玩家看见内部 key。
 */
function enumText(t: Translate, prefix: string, raw: unknown): string {
  const key = String(raw || "")
  if (!key) return "—"
  // 宿主 t() 在 key 缺失时返回 key 本身；这里用 defaultValue="" 把它变空串。
  const text = t(`${prefix}.${key}`, { defaultValue: "" })
  return text || "—"
}

/**
 * P8.1：空闲态的猫娘**开场嘲讽**候选键。
 *
 * 面板每 3 秒轮询一次，所以这个随机值必须**在组件挂载时定一次**，
 * 不能每次渲染都摇 —— 否则玩家会眼睁睁看着话术自己变。
 */
const IDLE_TAUNT_KEYS = [
  "panel.idle_taunt_1",
  "panel.idle_taunt_2",
  "panel.idle_taunt_3",
  "panel.idle_taunt_4",
  "panel.idle_taunt_5",
  "panel.idle_taunt_6",
]

type Stock = {
  id: string
  name: string
  /** P7.2：名册群友在面板上的显示名（「星辰·定海神针股」）。 */
  display_name?: string
  roster_alias?: string
  roster_title?: string
  price: number
  prev_price: number
  change: number
  tag: string
  tag_label: string
  tag_color: string
  alive: boolean
  kline?: KlinePoint[]
  /** P7.2：走势小折线的数据（降采样过）。 */
  spark?: KlinePoint[]
}

type Position = {
  stock_id: string
  name: string
  display_name?: string
  amount: number
  avg_cost: number
  price: number
  market_value: number
  unrealized_pnl: number
  sellable: number
  weight: number
  tag: string
  tag_label: string
  alive: boolean
}

type Catgirl = {
  alive: boolean
  cash: number
  positions_value: number
  position_count: number
  days_survived: number
  today_action: string
}

type NewsItem = {
  id: string
  kind: string
  kind_label: string
}

type PendingAction = {
  kind?: string
  stock_ids?: string[]
  amount?: number
  price?: number
  reason?: string
}

type AnalysisResult = {
  stock_id: string
  name: string
  price: number
  tag_label: string
  rating: string
  chasing: boolean
  position_weight: number
  market_note: string
  analyses_left: number
  note: string
}

/** P7：猫娘弹幕。 */
type Danmaku = {
  id?: number
  kind?: string
  text?: string
  at?: number
}

/** P7：社区名册里的群友。 */
type RosterMember = {
  stock_id?: string
  alias?: string
  ticker?: string
  title?: string
  persona?: string
  price?: number
  display_name?: string
}

/** P7.2：导演台的「赛博弹窗轰炸」。 */
type Boom = {
  active?: boolean
  count?: number
  seconds_left?: number
  until?: number
  /** P7.3：本次轰炸**本来该持续多久**（秒）—— 前端据此算自己的硬上限。 */
  duration?: number
}

/** P7：视觉降级三档 + 弹幕 + 导演台 + 名册（**纯表现层**）。 */
type Visual = {
  stage?: "rich" | "broke" | "dying"
  critical?: boolean
  critical_emoji?: string
  stage_thresholds?: {
    broke_day?: number
    dying_assets?: number
    panic_distance?: number
  }
  emoji_sea_size?: number
  danmaku?: Danmaku[]
  director?: { enabled?: boolean; queued?: number }
  boom?: Boom
  roster?: RosterMember[]
}

type State = {
  stage?: string
  day?: number
  tick?: number
  phase?: string
  ticks_per_day?: number
  ticks_to_settle?: number
  mode?: string
  mode_name?: string
  alive?: boolean
  cash?: number
  total_assets?: number
  /** P7.2：玩家自己的开局钱（用来算「到现在赚/亏了多少」）。 */
  initial_cash?: number
  living_cost?: number
  next_bill?: number
  bill_gap?: number
  missed_bills?: number
  distance_to_kill?: number | null
  concentration?: number
  positions?: Position[]
  stocks?: Stock[]
  kline_stock_ids?: string[]
  catgirl?: Catgirl | null
  news_today?: NewsItem[]
  pending_action?: PendingAction | null
  analyses_left?: number
  settlement?: Record<string, any> | null
  achievements?: Achievements | null
  notice?: string
  /** P7：视觉降级 / 弹幕 / 导演台 / 名册。 */
  visual?: Visual
}

/** P5.5 改动 4：天台虚惊一场的三屏。 */
type DeathScene = {
  screen1?: string
  screen2?: string
  screen3?: string
}

/** P5.5 改动 5/7：称号图鉴。 */
type Achievements = {
  unlocked?: { title?: string; epitaph?: string; identity_epitaph?: string }[]
  locked?: { title?: string; hint?: string }[]
  total?: number
  unlocked_count?: number
}

type AchievementRow = {
  unlocked: boolean
  title: string
  text: string
}

const RATING_TONE: Record<string, Tone> = {
  green: "success",
  yellow: "warning",
  red: "danger",
}

const PHASE_TONE: Record<string, Tone> = {
  honeymoon: "success",
  trap: "warning",
  slaughter: "danger",
}

/**
 * 涨绿跌红 —— P8.0 换成**柔和**配色：涨是薄荷绿，跌是珊瑚粉。
 *
 * 这两个值和 RRM_CSS 里的 ``--success`` / ``--danger`` 是**同一套**：
 * canvas 画不出来 CSS 变量（canvas 只认字面色值），所以这里写死同样的
 * 十六进制，两边必须同步改。
 */
const TREND_UP = "#5AC8A8"
const TREND_DOWN = "#FF6B81"

/**
 * P8.0：**清丽轻科技风**（从暗色调赛博朋克彻底翻面）。
 *
 * 做法（很重要，别写成逐组件覆写）：
 * 宿主的主题是靠 **CSS 变量**驱动的（`--text` / `--muted` / `--primary` /
 * `--success` / `--danger` / `--border` / `--surface` / `--surface-strong` /
 * `--radius-md`）。所以只要在 `.rrm-root` 上**重新定义这些变量**，宿主
 * 所有组件（卡片 / 按钮 / 徽标 / 文本 / 分隔线）会**一次性**换成浅色系 ——
 * 既不用碰宿主源码，也不用逐个组件写覆写。
 *
 * 剩下的几条硬规矩**一条都没破**（0.8.0 崩 UI 的五条根因全部规避）：
 *
 *   * 零 ``position``（absolute / fixed）、零 ``inset``、零 ``z-index``；
 *   * 零 ``filter``（所以**真毛玻璃做不了** —— 毛玻璃靠 ``backdrop-filter``，
 *     属于 filter 家族。这里用「半透明白 + 淡阴影 + 16px 圆角」做**质感近似**，
 *     视觉上是清透的，但绝不会给祖先建立 containing block）；
 *   * 零 ``opacity``（半透明一律用 ``rgba`` 表达）；
 *   * 零伪元素（``content:`` 一律不写）；
 *   * 唯一的 ``animation`` 是 ``animation: none !important``（关掉宿主入场淡入）；
 *   * 渐变**只允许纵向**（180deg）—— 0.8.0 的巨大 X 线条是 ``position:absolute
 *     + inset:0 + 对角渐变`` 做的，这里既没有定位也没有对角渐变。
 */
const RRM_CSS = `
*, *::before, *::after { box-sizing: border-box; }
.rrm-root, .rrm-root *, .rrm-root *::before, .rrm-root *::after { animation: none !important; }
.rrm-root .neko-stack > *, .rrm-root .neko-card-body > * { min-width: 0; }
.rrm-root .neko-key-value-value { overflow-wrap: anywhere; }

/* ── ① 主题变量覆写：一次改动，宿主所有组件跟着变浅 ─────────────── */
.rrm-root {
  --text: #22304a;
  --muted: #6b7a90;
  --primary: #4c7df0;
  --success: #5ac8a8;
  --danger: #ff6b81;
  --border: rgba(148, 163, 184, 0.26);
  --surface: rgba(255, 255, 255, 0.72);
  --surface-strong: #ffffff;
  --bg: #eef4fb;
  --radius-md: 16px;
  background: linear-gradient(180deg, #e9f1fa 0%, #f6fafe 42%, #ffffff 100%);
  color: var(--text);
}

/* ── ② 卡片：半透明白 + 16px 圆角 + 极淡阴影 ──────────────────────── */
.rrm-root .neko-card {
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 16px;
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.06);
}
.rrm-root .neko-card-title { color: #1f2a44; font-weight: 760; }
.rrm-root .neko-heading { color: #1f2a44; }
.rrm-root .neko-text { color: #6b7a90; }
.rrm-root .neko-badge {
  background: rgba(255, 255, 255, 0.86);
  border-color: rgba(148, 163, 184, 0.28);
  color: #55637a;
}

/* ── ③ 按钮：胶囊化 + 柔和色调 ───────────────────────────────────── */
.rrm-root .neko-button {
  border-radius: 999px;
  background: rgba(76, 125, 240, 0.10);
  border-color: rgba(76, 125, 240, 0.30);
  color: #3563d4;
  font-weight: 700;
}
.rrm-root .neko-button[data-tone="success"] {
  background: rgba(90, 200, 168, 0.14);
  border-color: rgba(90, 200, 168, 0.44);
  color: #2e9e80;
}
.rrm-root .neko-button[data-tone="danger"] {
  background: rgba(255, 107, 129, 0.12);
  border-color: rgba(255, 107, 129, 0.42);
  color: #e04e66;
}

/* ── ④ 操作台的两个大胶囊按钮（各占一半宽，带一点微光）────────────── */
.rrm-root .neko-button.rrm-pill {
  flex: 1 1 0;
  min-width: 0;
  padding: 13px 14px;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 1px;
  border-radius: 999px;
  /* 真机教训：按钮被压窄时中文会**竖着排**（一字一行），难看且读不出来。
     锁死不换行，宁可两行空间也要横着排。 */
  white-space: nowrap;
}
/* ActionButton 会把按钮包进一个 div（class 名 neko-action-control），
   而那个壳在宿主里是 inline-grid（收缩到内容宽）—— 不改它的话，
   网格里的列会被撑歪，两个按钮就不是各占一半。
   这里把壳改成 grid（撑满列），里面的按钮再 width: 100%。
   注意：这段 CSS 在反引号模板字符串里，**注释里绝不能出现反引号**。 */
.rrm-root .neko-action-control {
  display: grid;
  align-items: stretch;
  min-width: 0;
}
.rrm-root .neko-action-control > .neko-button.rrm-pill { width: 100%; }

/* P8.1：两个胶囊按钮**紧凑并排**，各占 48% 宽（各留 2% + 间距在中间），
   整排**居中** —— 0.9.0 的 1fr 两列看着太散，像两张不相干的卡片。 */
.rrm-root .neko-grid.rrm-trade-row {
  grid-template-columns: repeat(2, minmax(0, 48%));
  justify-content: center;
}

/* P8.1：标的下拉框别孤零零横跨整屏 —— 限宽 + 居中。 */
.rrm-root .neko-inline.rrm-select-row { justify-content: center; }
.rrm-root .neko-inline.rrm-select-row .neko-select { max-width: 360px; }

/* P8.1：K 线卡的标的框同样限宽。它这行左边顶着卡片标题、右边跟着
   「拉这只的K线」按钮和行情文案，所以**不居中**（居中会和左对齐的卡片
   标题脱节），只把满宽的下拉框收成 360px。 */
.rrm-root .neko-inline.rrm-kline-row .neko-select { max-width: 360px; }

/* P8.1：K 线数据不足时的提示语 —— 浅灰、居中、不抢戏。 */
.rrm-root .rrm-kline-warming {
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.rrm-root .neko-button.rrm-pill-buy {
  background: rgba(90, 200, 168, 0.16);
  border-color: rgba(90, 200, 168, 0.46);
  color: #1f8a6e;
  box-shadow: 0 8px 22px rgba(90, 200, 168, 0.18);
}
.rrm-root .neko-button.rrm-pill-sell {
  background: rgba(255, 107, 129, 0.14);
  border-color: rgba(255, 107, 129, 0.46);
  color: #e04e66;
  box-shadow: 0 8px 22px rgba(255, 107, 129, 0.18);
}

/* ── ⑤ 辅助按钮：缩小、变轻 ─────────────────────────────────────── */
.rrm-root .neko-button.rrm-mini {
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 650;
}

/* ── ⑥ 群友头像（网格卡片里的小圆头像）──────────────────────────── */
.rrm-root .rrm-avatar {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(255, 255, 255, 0.9);
  vertical-align: middle;
}
.rrm-root .rrm-stock-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
  font-size: 14px;
  color: #22304a;
}

/* ── ⑦ P7.2 遗留的三档字体（只改字体，不改布局）──────────────────── */
.rrm-font-rich { font-family: "YouYuan", "Yuanti SC", "Hiragino Maru Gothic ProN", "Microsoft YaHei UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
.rrm-font-broke { font-weight: 300; }
.rrm-font-dying { font-weight: 500; }
.rrm-root .neko-button.rrm-escape { font-size: 11px; padding: 2px 8px; color: var(--muted); }
`

function money(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  return Number(value).toFixed(2)
}

/**
 * P7.3：网贷弹窗的**本地超时**（秒）。
 *
 * 0.8.2 真机事故：弹窗唯一能关掉的路径是按钮的 `onResult`，而宿主
 * `ActionButton` 把 `onResult` 挡在 `await props.api.refresh()` 后面 ——
 * 刷新一失败，回调永远不来，弹窗永远关不掉，玩家被锁死在面板里。
 *
 * 现在有三道保险，任何一道都能把玩家放出来：
 *   1. **同步关窗**（点按钮的那一瞬间就把弹窗关掉，不等任何回调）；
 *   2. **本地倒计时**（本常量）：30 秒没人操作就自己关窗；
 *   3. **服务端看门狗**：30 秒没人操作，服务端自动结算
 *      （无限模式强制借入 / 经典模式直接破产）。
 */
const LOAN_PROMPT_TIMEOUT_SECONDS = 30

/**
 * P7.3：赛博弹窗轰炸的**客户端硬上限**（毫秒）。
 *
 * 0.8.2 用服务端的 `until` 时间戳减**客户端时钟**算剩余时间 —— 时钟偏了、
 * 或者面板载荷停止刷新，`boom.active` 就会永远为 true ⇒ 永久锁屏。
 *
 * 现在改成：从**第一次看见它**起，本地记一个截止时刻，到点无条件关窗。
 * 服务端说该持续多久就持续多久（`boom.duration`），但**只认第一次**，
 * 之后载荷再怎么刷都不会把这个上限往后推。
 */
const BOOM_HARD_LIMIT_MS = 5000

/**
 * P7.4：面板**自己轮询**上下文的节奏（毫秒）。
 *
 * 宿主**从不轮询** Hosted 面板的上下文 —— 它只在「插件启停 / 重载」和
 * 「面板自己发起动作之后」刷新。对一个每秒 tick 的实时游戏面板来说，这带来
 * 两个真问题：
 *
 * 1. **tick 是死的**：面板看着不动，玩家不知道行情在走；
 * 2. **面板会变陈旧**：这一局结束（或被别人结束）之后，面板还停在局中界面，
 *    玩家点「一键梭哈」只会拿到后端的 `not_joined`，而宿主把**任何**动作失败
 *    都当成「面板运行时错误」上报 —— 屏幕顶部弹一个黄色的
 *    「插件界面控件错误」覆盖层（0.8.3 真机事故）。
 *
 * 所以这里自己按固定节奏拉一次上下文。宿主的 `render` 走的是 reconcile
 * （组件实例不重建），本地状态（弹窗、倒计时、逃生确认）不会被打断。
 */
const PANEL_POLL_INTERVAL_MS = 3000

function percent(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  return `${(Number(value) * 100).toFixed(1)}%`
}

/** 涨跌数字：带正负号，交给样式表上色。 */
function changeText(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  const pct = Number(value) * 100
  const sign = pct > 0 ? "+" : ""
  return `${sign}${pct.toFixed(2)}%`
}

/** 涨绿跌红。 */
function changeColor(value: number | undefined | null): string {
  const pct = Number(value || 0) * 100
  if (pct > 0.001) return "var(--success)"
  if (pct < -0.001) return "var(--danger)"
  return "var(--muted)"
}

/**
 * P7.2：**超大号**的统计数字。
 *
 * 宿主 `StatCard` **不透传 style**（见 runtime.js），所以要自己拼一个
 * `.neko-stat`（沿用宿主自己的类，样式还是宿主的）。
 */
function BigStat(props: {
  label: string
  value: string
  hint?: string
  color?: string
  size?: number
}) {
  return h(
    "div",
    { className: "neko-stat" },
    h("span", { className: "neko-stat-label" }, props.label),
    h(
      "strong",
      {
        className: "neko-stat-value",
        style: {
          fontSize: `${props.size || 34}px`,
          color: props.color,
          lineHeight: "1.15",
        },
      },
      props.value
    ),
    props.hint
      ? h("span", { style: { fontSize: "12px", color: "var(--muted)" } }, props.hint)
      : null
  )
}

/**
 * P7.2：走势小折线（Sparkline）。
 *
 * 宿主渲染器画不了真 SVG，但 **canvas 是可以用的**（`document.createElement`
 * 建出来的是真 canvas，`ref` 也会挂到真实 DOM 节点上）。所以这里用 canvas
 * 画一条折线；万一宿主环境拿不到 2D 上下文，就**降级成柱子**（纯 div，
 * 不依赖任何定位 / 动画），绝不抛错、绝不崩面板。
 */
function Sparkline(props: { points: number[]; up: boolean; title?: string }) {
  const canvasRef = useRef<any>(null)
  const [broken, setBroken] = useState(false)
  const values = props.points || []
  const signature = values.join(",")

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) {
      setBroken(true)
      return
    }
    let ctx: any = null
    try {
      ctx = canvas.getContext("2d")
    } catch (error) {
      ctx = null
    }
    if (!ctx) {
      setBroken(true)
      return
    }
    try {
      const width = Number(canvas.width) || 84
      const height = Number(canvas.height) || 26
      ctx.clearRect(0, 0, width, height)
      if (values.length === 0) return
      let max = values[0]
      let min = values[0]
      for (const value of values) {
        if (value > max) max = value
        if (value < min) min = value
      }
      const span = max - min || 1
      const pad = 2
      const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0
      ctx.strokeStyle = props.up ? TREND_UP : TREND_DOWN
      ctx.lineWidth = 1.6
      ctx.beginPath()
      values.forEach((value, index) => {
        const x = pad + step * index
        const y = height - pad - ((value - min) / span) * (height - pad * 2)
        if (index === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      })
      ctx.stroke()
    } catch (error) {
      setBroken(true)
    }
  }, [signature, props.up])

  if (broken) {
    // 兜底：画不了 canvas 就用柱子（纯 div，无背景无定位）。
    const last = values.slice(-12)
    const max = last.length ? Math.max.apply(null, last) : 0
    const min = last.length ? Math.min.apply(null, last) : 0
    const span = max - min || 1
    const bars = last.map((value, index) => {
      const previous = index > 0 ? last[index - 1] : value
      return h("div", {
        key: index,
        style: {
          width: "3px",
          height: `${Math.max(2, Math.round(((value - min) / span) * 18))}px`,
          marginRight: "1px",
          background: value >= previous ? TREND_UP : TREND_DOWN,
        },
      })
    })
    return h(
      "div",
      {
        title: props.title,
        style: {
          display: "flex",
          alignItems: "flex-end",
          height: "20px",
        },
      },
      ...bars
    )
  }

  return h("canvas", {
    ref: canvasRef,
    width: 84,
    height: 26,
    title: props.title,
    style: { display: "block" },
  })
}

/**
 * P8.0：**群友头像**。
 *
 * 社区名册里的群友（星辰 / Wehos / 老王 / 小明 / 熬夜怪 / 潜水员）在行情
 * 方块卡里挂一个小圆头像 —— 头像是 ``ui/assets/`` 里内联的 PNG（data URI）。
 *
 * 极端情况下（图片解码失败）**退回专属 Emoji**，绝不出现破图。
 */
function RosterAvatar(props: { alias?: string }) {
  const alias = props.alias || ""
  const entry = AVATARS[alias]
  const [broken, setBroken] = useState(false)
  if (!entry || broken) {
    return h(
      "span",
      { style: { fontSize: "14px", lineHeight: "1" } },
      entry ? entry.fallback : "🐱"
    )
  }
  return h("img", {
    src: entry.src,
    alt: alias,
    className: "rrm-avatar",
    onError: () => setBroken(true),
  })
}

/**
 * P8.0：**行情方块卡片**（网格单元）。
 *
 * 一格 = 股票名（+ 群友头像）+ 微型折线 + **巨大**涨跌百分比 + 现价。
 *
 * 用宿主 `Card` 组件 ⇒ 它是网格容器的**直接**子元素（0.8.0 的第四条根因
 * 就是自定义 div 包裹层把卡片挤出 flex 流），布局由宿主负责，不会挤成一团。
 *
 * 三个信息维度（走势 / 现价 / 涨跌）的标签走 i18n，用原生 ``title`` 属性
 * 当悬浮提示 —— 不加任何 CSS，也不破坏方块卡的紧凑排版。
 */
function StockTile(props: { stock: Stock; t: Translate }) {
  const stock = props.stock
  const t = props.t
  const up = Number(stock.change || 0) >= 0
  return h(
    Card,
    { className: "rrm-tile" },
    h(
      Stack,
      { gap: 6 },
      h(
        "span",
        { className: "rrm-stock-name" },
        h(RosterAvatar, { alias: stock.roster_alias }),
        stock.display_name || stock.name
      ),
      h(Sparkline, {
        points: (stock.spark || []).map((point) => point[1]),
        up,
        title: t("visual.trend_title"),
      }),
      h(
        "span",
        {
          title: t("visual.change_title"),
          style: {
            fontSize: "26px",
            fontWeight: 800,
            color: changeColor(stock.change),
            whiteSpace: "nowrap",
            lineHeight: "1.1",
          },
        },
        changeText(stock.change)
      ),
      h(
        "span",
        {
          title: t("visual.price_title"),
          style: { fontSize: "12px", color: "var(--muted)" },
        },
        money(stock.price)
      )
    )
  )
}

/**
 * P8.0：**持仓方块卡片**（网格单元）。
 *
 * 和行情方块卡同一套视觉：名字 + 大号盈亏 + 当前市值。
 */
function PositionTile(props: { position: Position; t: Translate }) {
  const position = props.position
  const t = props.t
  const pnl = Number(position.unrealized_pnl || 0)
  const color =
    pnl > 0.001 ? "var(--success)" : pnl < -0.001 ? "var(--danger)" : "var(--muted)"
  return h(
    Card,
    { className: "rrm-tile" },
    h(
      Stack,
      { gap: 4 },
      h(
        "span",
        { className: "rrm-stock-name" },
        position.display_name || position.name
      ),
      h(
        "span",
        {
          title: t("visual.pnl_title"),
          style: {
            fontSize: "26px",
            fontWeight: 800,
            color,
            whiteSpace: "nowrap",
            lineHeight: "1.1",
          },
        },
        `${pnl > 0 ? "+" : ""}${money(pnl)}`
      ),
      h(
        "span",
        { style: { fontSize: "12px", color: "var(--muted)" } },
        money(position.market_value)
      )
    )
  )
}

/**
 * K 线：用 div 拼出来的迷你蜡烛图（宿主画不了 SVG）。
 *
 * P8.1：**刚开盘不再画一根顶天立地的柱子。**
 *
 * 0.9.0 的 K 线高度是按「价格区间」归一化的（min→2px，max→满格）。
 * 点数少的时候区间极窄，于是**最后一根柱子永远是满格** ——
 * 开局第一眼就是一根又高又孤立的绿柱，极其突兀。
 *
 * 现在点数不足 ``MIN_KLINE_POINTS`` 时**不画柱子**，改成在图表区正中
 * 摆一句浅灰提示（「市场刚开盘，积累数据中……」），等点数攒够了自然接管。
 */
const MIN_KLINE_POINTS = 5

function Kline(props: {
  points: KlinePoint[]
  emptyText: string
  warmingText: string
}) {
  const points = props.points || []
  // P8.0：K 线是**上半区的主视觉** —— 更高、更抢眼。
  const height = 232
  if (points.length === 0) {
    return h("div", null, h("span", null, props.emptyText))
  }
  if (points.length < MIN_KLINE_POINTS) {
    return h(
      "div",
      {
        className: "rrm-kline-warming",
        style: {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: `${height}px`,
        },
      },
      h("span", null, props.warmingText)
    )
  }
  const prices = points.map((point) => point[1])
  const max = Math.max.apply(null, prices)
  const min = Math.min.apply(null, prices)
  const span = max - min || 1
  // 0.8.1：不再按档位改 K 线颜色（家道中落的视觉降级已回滚，见 RRM_CSS 注释）。
  const up = TREND_UP
  const down = TREND_DOWN
  const bars = points.map((point, index) => {
    const tick = point[0]
    const price = point[1]
    const barHeight = Math.max(2, Math.round(((price - min) / span) * (height - 16)))
    const previous = index > 0 ? points[index - 1][1] : price
    const color = price >= previous ? up : down
    return h("div", {
      key: tick,
      title: `tick ${tick} · ${price.toFixed(4)}`,
      style: {
        flex: "1 1 0",
        minWidth: "3px",
        maxWidth: "16px",
        height: `${barHeight}px`,
        marginRight: "2px",
        background: color,
        borderRadius: "2px",
      },
    })
  })
  return h(
    "div",
    {
      style: {
        display: "flex",
        alignItems: "flex-end",
        height: `${height}px`,
        padding: "6px 0",
        overflowX: "auto",
      },
    },
    ...bars
  )
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, actions } = props

  const [selectedStock, setSelectedStock] = useLocalState<string>("rrm.selected_stock", "")
  const [sellStock, setSellStock] = useLocalState<string>("rrm.sell_stock", "")
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null)
  const [newsDetail, setNewsDetail] = useState<Record<string, any> | null>(null)
  const [dismissedProposal, setDismissedProposal] = useState<string>("")
  // ── P8.1：空闲态的猫娘**开场嘲讽** ──────────────────────────────
  // 面板每 3 秒轮询一次，所以随机值必须**在挂载时摇一次**并锁住 ——
  // 否则玩家会眼睁睁看着话术每 3 秒自己换一句（比没有还瘆人）。
  const [idleTauntKey] = useState<string>(() => {
    const index = Math.floor(Math.random() * IDLE_TAUNT_KEYS.length)
    return IDLE_TAUNT_KEYS[index] || IDLE_TAUNT_KEYS[0]
  })
  const idleTaunt = t(idleTauntKey)
  // ── P6 档 2：按需拉到的 K 线（局中载荷只带前 20 只，其余现拉）────
  const [klineDetail, setKlineDetail] = useState<Record<string, any> | null>(null)
  // ── P5.5 改动 4：天台三屏（1 → 2 → 3，间隔自动推进）─────────────
  const [sceneScreen, setSceneScreen] = useState<number>(1)
  // ── P5.5 改动 7：称号图鉴的折叠状态（持久化）────────────────────
  const [achievementsOpen, setAchievementsOpen] = useLocalState<string>(
    "rrm.achievements_open",
    "0"
  )
  // ── P7：导演台（长按标题栏）、网贷弹窗 ──────────────────────────
  const [directorOpen, setDirectorOpen] = useState(false)
  const [holdHint, setHoldHint] = useState(false)
  const [loanModalOpen, setLoanModalOpen] = useState(false)
  const [loanModalDay, setLoanModalDay] = useState<number>(-1)
  // ── P7.3：网贷弹窗的本地倒计时（秒）+ 标题栏逃生按钮的两步确认 ──────
  const [loanLeft, setLoanLeft] = useState<number>(0)
  const [escapeArmed, setEscapeArmed] = useState(false)
  const escapeTimer = useRef<any>(null)
  // P7.3：网贷弹窗的**本地截止时刻**（epoch 毫秒）与轰炸的**硬上限**。
  const loanDeadline = useRef<number>(0)
  const boomDeadline = useRef<number>(0)
  // ── P7.4：面板自己轮询上下文（宿主不轮询）────────────────────────
  useEffect(() => {
    let cancelled = false
    const timer = setInterval(() => {
      if (cancelled) return
      try {
        const pending = props.api.refresh()
        if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
      } catch (_error) {
        // 插件正在重启：拉不到就算了，下一个周期再说。
      }
    }, PANEL_POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [props.api])
  // ── P7.2：濒死档「数字偶尔闪红」（**JS 定时器**，不用 CSS 动画）──
  const [flashOn, setFlashOn] = useState(false)
  // ── P7.2：弹窗轰炸的倒计时心跳（用来把 5 秒倒计时画出来）─────────
  const [, setBoomTick] = useState(0)
  const holdTimer = useRef<any>(null)

  const visual = state.visual || {}
  const stage = visual.stage || "rich"
  const critical = Boolean(visual.critical)
  const directorEnabled = Boolean((visual.director || {}).enabled)
  const danmakuList = visual.danmaku || []
  const rosterList = visual.roster || []
  const boom = visual.boom || {}

  // 三屏自动推进：第一屏停 3 秒（黑屏），之后每屏 2 秒。
  const deathScene = (state.settlement && state.settlement.death_scene) || null
  useEffect(() => {
    if (!deathScene || sceneScreen >= 3) return
    const delay = sceneScreen === 1 ? 3000 : 2000
    const timer = setTimeout(() => setSceneScreen((current) => current + 1), delay)
    return () => clearTimeout(timer)
  }, [sceneScreen, deathScene])

  // ── P7：荒诞弹窗 —— 交不起饭钱时弹一次「赛博网贷广告」───────────
  useEffect(() => {
    if (state.stage !== "playing") return
    if ((state.bill_gap || 0) <= 0) return
    const day = state.day || 0
    if (loanModalDay === day) return
    setLoanModalDay(day)
    setLoanModalOpen(true)
    // P7.3：告诉服务端「弹窗开了」——服务端据此启动 30 秒超时看门狗。
    // （服务端在渲染载荷时也会自己登记一遍，这里是双保险。）
    try {
      props.api
        .call("loan_prompt", { choice: "open" }, { userInitiated: true })
        .catch(() => undefined)
    } catch (_error) {
      // 登记失败也不要紧：服务端还有载荷侧登记 + 前端本地倒计时兜底。
    }
  }, [state.bill_gap, state.day, state.stage, loanModalDay, props.api])

  // ── P7.3：网贷弹窗的**本地倒计时**（前端兜底）────────────────────
  // 30 秒内没人操作 → 本地自己把弹窗关掉（服务端看门狗同时会自动结算）。
  // 计时与关窗**写在同一个定时器里**，避免两个 effect 抢同一个状态。
  useEffect(() => {
    if (!loanModalOpen) {
      loanDeadline.current = 0
      setLoanLeft(0)
      return undefined
    }
    if (loanDeadline.current <= 0) {
      loanDeadline.current = Date.now() + LOAN_PROMPT_TIMEOUT_SECONDS * 1000
    }
    setLoanLeft(LOAN_PROMPT_TIMEOUT_SECONDS)
    const timer = setInterval(() => {
      const left = Math.max(
        0,
        Math.ceil((loanDeadline.current - Date.now()) / 1000)
      )
      setLoanLeft(left)
      if (left <= 0) {
        // 到点了：本地关窗（**不动** loanModalDay —— 今天不再自动重开）。
        setLoanModalOpen(false)
      }
    }, 500)
    return () => clearInterval(timer)
  }, [loanModalOpen])

  // ── P7：长按标题栏 → 露出导演台（只在特制版）────────────────────
  const startHold = () => {
    if (!directorEnabled) return
    if (holdTimer.current) return
    setHoldHint(true)
    holdTimer.current = setTimeout(() => {
      holdTimer.current = null
      setHoldHint(false)
      setDirectorOpen(true)
    }, 1200)
  }
  const endHold = () => {
    if (holdTimer.current) {
      clearTimeout(holdTimer.current)
      holdTimer.current = null
    }
    setHoldHint(false)
  }
  useEffect(() => () => endHold(), [])

  // ── P7.2：濒死档「数字偶尔闪红」 ────────────────────────────────
  // **不用 CSS 动画**（0.8.0 的崩溃根因之一）：用 JS 定时器把颜色切一下。
  useEffect(() => {
    if (stage !== "dying") {
      setFlashOn(false)
      return
    }
    let hideTimer: any = 0
    const timer = setInterval(() => {
      setFlashOn(true)
      hideTimer = setTimeout(() => setFlashOn(false), 320)
    }, 2600)
    return () => {
      clearInterval(timer)
      clearTimeout(hideTimer)
    }
  }, [stage])

  // ── P7.3：弹窗轰炸的**客户端硬上限**（不依赖载荷刷新）───────────
  // 0.8.2 用服务端的 `until` 时间戳减**客户端时钟**算剩余时间：时钟一偏、
  // 或者面板载荷停止刷新，`boom.active` 就会永远为 true ⇒ 永久锁屏。
  // 现在改成：**第一次看见它**的时候本地记一个截止时刻，到点无条件关窗。
  const boomPayloadActive = Boolean(boom.active)
  const boomWindow = Math.min(30, Math.max(1, Number(boom.duration || 5) || 5))
  useEffect(() => {
    if (!boomPayloadActive) {
      boomDeadline.current = 0
      return
    }
    if (boomDeadline.current <= 0) {
      boomDeadline.current = Date.now() + boomWindow * 1000
    }
  }, [boomPayloadActive, boomWindow])
  useEffect(() => {
    if (!boomPayloadActive) return
    const timer = setInterval(() => setBoomTick((count) => count + 1), 300)
    return () => clearInterval(timer)
  }, [boomPayloadActive])
  const boomLeft = Math.max(0, (boomDeadline.current - Date.now()) / 1000)
  const boomActive = boomPayloadActive && boomLeft > 0

  const stocks = state.stocks || []
  const positions = state.positions || []
  // P8.0：行情网格最多铺 12 张方块卡（再多是视觉噪音，不是信息）。
  const visibleStocks = stocks.slice(0, 12)
  const hiddenStockCount = Math.max(0, stocks.length - visibleStocks.length)
  const news = state.news_today || []
  const catgirl = state.catgirl || null
  const pending = state.pending_action || null
  const klineIds = state.kline_stock_ids || []

  const actionById = (id: string): HostedAction | undefined =>
    (actions || []).find((item) => item.id === id)

  // ── P7.4：动作失败后把上下文拉回来 ────────────────────────────
  // 宿主的上下文不轮询，不主动拉的话面板会一直停在旧状态
  // （比如这一局已经结束了，屏幕上的按钮全是死的）。
  const resync = () => {
    try {
      const pending = props.api.refresh()
      if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
    } catch (_error) {
      // 拉不到就算了。
    }
  }

  // 动作失败：先给人一句人话，再把上下文拉回来。
  const onActionError = (error: any) => {
    showToast(String((error && error.message) || error), "danger")
    resync()
  }

  const stockOptions = stocks
    .filter((stock) => stock.alive)
    .map((stock) => ({
      value: stock.id,
      label: stock.display_name || stock.name,
    }))

  const positionOptions = positions.map((position) => ({
    value: position.stock_id,
    label: position.display_name || position.name,
  }))

  // 没选过就默认第一只活着的标的。
  const effectiveStockId =
    selectedStock && stockOptions.some((option) => option.value === selectedStock)
      ? selectedStock
      : stockOptions.length > 0
        ? stockOptions[0].value
        : ""
  const effectiveSellStockId =
    sellStock && positionOptions.some((option) => option.value === sellStock)
      ? sellStock
      : positionOptions.length > 0
        ? positionOptions[0].value
        : ""

  // K 线标的：局中载荷只带前 20 只（限流），其余用「按需拉取」入口补。
  const klineStockId =
    selectedStock && stockOptions.some((option) => option.value === selectedStock)
      ? selectedStock
      : klineIds.length > 0
        ? klineIds[0]
        : effectiveStockId
  const klineStock = stocks.find((stock) => stock.id === klineStockId)
  const bundledKline = (klineStock && klineStock.kline) || []
  const fetchedKline =
    klineDetail && klineDetail.stock_id === klineStockId ? klineDetail.kline || [] : []
  const klinePoints = bundledKline.length > 0 ? bundledKline : fetchedKline

  const proposalKey = pending
    ? `${pending.kind || ""}:${(pending.stock_ids || []).join(",")}:${pending.amount || 0}`
    : ""
  const showPawnbroker =
    Boolean(pending) &&
    pending!.kind === "pawnbroker_sell" &&
    proposalKey !== "" &&
    proposalKey !== dismissedProposal

  // ── P7：三档的"资金"叫法 ────────────────────────────────────────
  const cashLabel =
    stage === "dying"
      ? t("visual.cash_dying")
      : stage === "broke"
        ? t("visual.cash_broke")
        : t("visual.cash_rich")
  // P7.2：总资产按档位换名字（零花钱 / 家底 / 棺材本）。
  const assetsLabel =
    stage === "dying"
      ? t("visual.assets_dying")
      : stage === "broke"
        ? t("visual.assets_broke")
        : t("visual.assets_rich")
  // P7.2：档位提示语（老爸给的钱 / 要交房租了 / 下顿饭的钱）。
  const stageHint =
    stage === "dying"
      ? t("visual.hint_dying")
      : stage === "broke"
        ? t("visual.hint_broke")
        : t("visual.hint_rich")
  const stageLabel =
    stage === "dying"
      ? t("visual.stage_dying")
      : stage === "broke"
        ? t("visual.stage_broke")
        : t("visual.stage_rich")
  // P7.2：字体档位（只改字体，不改布局）。
  const stageClass =
    stage === "dying" ? "rrm-font-dying" : stage === "broke" ? "rrm-font-broke" : "rrm-font-rich"

  // ── P7.2：傻子版的第一屏数字 ────────────────────────────────────
  const totalAssets = Number(state.total_assets || 0)
  const initialCash = Number(state.initial_cash || 0)
  const pnl = totalAssets - initialCash
  const pnlColor = pnl > 0.001 ? "var(--success)" : pnl < -0.001 ? "var(--danger)" : "var(--muted)"
  const pnlHint =
    pnl > 0.001 ? t("visual.pnl_up") : pnl < -0.001 ? t("visual.pnl_down") : t("visual.pnl_flat")
  // 濒死档：数字**偶尔闪红**（用 JS 定时器切颜色，不用 CSS 动画）。
  const flashColor = flashOn ? "var(--danger)" : undefined

  // ── P7.2：一键梭哈 / 一键割肉的数量 ─────────────────────────────
  const selectedStockRow = stocks.find((stock) => stock.id === effectiveStockId)
  const buyAllAmount =
    selectedStockRow && selectedStockRow.price > 0
      ? Math.floor((Math.max(0, Number(state.cash || 0)) * 0.995) / selectedStockRow.price)
      : 0
  const selectedPosition = positions.find((item) => item.stock_id === effectiveSellStockId)
  const sellAllAmount = selectedPosition ? Number(selectedPosition.sellable || 0) : 0

  // ── P7：终局 Emoji 海 ───────────────────────────────────────────
  const emojiSea = useMemo(() => {
    const size = Math.max(50, Number(visual.emoji_sea_size || 60))
    const items: string[] = []
    for (let index = 0; index < size; index += 1) {
      items.push(index % 2 === 0 ? "🤡" : "🪦")
    }
    return items
  }, [visual.emoji_sea_size])

  // ── P7.2：赛博弹窗轰炸（关不掉，5 秒）───────────────────────────
  const boomModal = boomActive
    ? h(Modal, {
        open: true,
        title: t("visual.boom_title"),
        // **点遮罩 / 按 ESC 都关不掉** —— 只能等它自己倒计时结束。
        closeOnBackdrop: false,
        closeOnEscape: false,
        size: "lg",
        children: h(
          Stack,
          { gap: 8 },
          h(Text, null, t("visual.boom_body", { count: boom.count || 50 })),
          h(Alert, {
            tone: "warning",
            message: t("visual.boom_countdown", { seconds: Math.ceil(boomLeft) }),
          }),
          h(
            Grid,
            { cols: 10, gap: 2 },
            Array.from({ length: Number(boom.count || 50) }, (_item, index) =>
              h(
                "span",
                { key: index, style: { fontSize: "22px", lineHeight: "1.1" } },
                "🤡"
              )
            )
          )
        ),
      })
    : null

  // ── P7.3：标题栏右上角的**紧急逃生**（两步内联确认）────────────
  // 用途：处理所有不可预见的 UI 卡死（弹窗锁死、载荷不刷新、按钮点不动）。
  // 刻意**不用 Modal** 做二次确认 —— 万一面板已经卡死，再弹一个 Modal
  // 只会更糟；两步点确认（3 秒内再点一次）足够防误触。
  const escapeGame = () => {
    try {
      props.api
        .call("declare_bankrupt", {}, { userInitiated: true })
        .then(async () => {
          await props.api.refresh()
        })
        .catch((error: any) => {
          showToast(String((error && error.message) || error), "danger")
        })
    } catch (_error) {
      // 连调用都抛出来了：面板已经彻底坏了 —— 玩家只能手动重启插件。
    }
  }

  const armEscape = () => {
    if (escapeArmed) {
      if (escapeTimer.current) {
        clearTimeout(escapeTimer.current)
        escapeTimer.current = null
      }
      setEscapeArmed(false)
      escapeGame()
      return
    }
    setEscapeArmed(true)
    if (escapeTimer.current) clearTimeout(escapeTimer.current)
    escapeTimer.current = setTimeout(() => setEscapeArmed(false), 3000)
  }
  useEffect(
    () => () => {
      if (escapeTimer.current) clearTimeout(escapeTimer.current)
    },
    []
  )

  // ── P7：标题栏（长按露出导演台）─────────────────────────────────
  // 0.8.1：**只保留布局**，不再有背景 / 边框 / 圆角 —— 那层深色半透明底
  // 在宿主深浅色主题下都会让整块面板显脏。
  const titleBar = h(
    "div",
    {
      style: {
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "8px",
        flexWrap: "wrap",
      },
      onPointerDown: startHold,
      onPointerUp: endHold,
      onPointerLeave: endHold,
      onPointerCancel: endHold,
      onMouseDown: startHold,
      onMouseUp: endHold,
      onMouseLeave: endHold,
      onContextMenu: (event: any) => {
        if (event && typeof event.preventDefault === "function") event.preventDefault()
      },
    },
    h("span", null, `${t("panel.title")} · ${stageLabel}`),
    // 开源纯净版**不剧透**导演台：连提示都不给（入口本身插件层还会再挡一次）。
    directorEnabled
      ? h(
          "span",
          { style: { fontSize: "12px", opacity: holdHint ? 0.9 : 0.6 } },
          holdHint ? t("visual.director_hold") : t("visual.director_hint")
        )
      : null,
    // ── P7.3：紧急逃生（极小、不显眼；点两下才真退出）────────────
    state.stage === "playing"
      ? h(
          Button,
          {
            className: "rrm-escape",
            tone: "default",
            onClick: (event: any) => {
              if (event && typeof event.stopPropagation === "function") {
                event.stopPropagation()
              }
              armEscape()
            },
            onPointerDown: (event: any) => {
              if (event && typeof event.stopPropagation === "function") {
                event.stopPropagation()
              }
            },
            children: escapeArmed
              ? t("visual.escape_confirm")
              : t("visual.escape_exit"),
          }
        )
      : null
  )

  // ── P7：导演台（只在特制版；入口本身也会在插件层再挡一次）──────
  const directorConsole = directorOpen
    ? h(
        Card,
        { title: t("visual.director_title") },
        h(
          Stack,
          { gap: 8 },
          h(Alert, {
            tone: "warning",
            message: t("visual.director_locked"),
          }),
          h(
            Inline,
            { gap: 8, wrap: true, align: "center" },
            h(
              Select,
              {
                value: effectiveStockId,
                options: stockOptions,
                onChange: (value: any) => setSelectedStock(String(value)),
              }
            ),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_crash"),
              tone: "danger",
              values: { op: "crash", stock_id: effectiveStockId },
              onError: onActionError,
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_loan"),
              tone: "danger",
              values: { op: "loan" },
              onError: onActionError,
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_welfare"),
              tone: "danger",
              values: { op: "welfare" },
              onError: onActionError,
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_kill"),
              tone: "danger",
              values: { op: "kill" },
              onError: onActionError,
            }),
            // P7.2：赛博弹窗轰炸（关不掉的 Emoji 海 + 封锁操作 5 秒）。
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_boom"),
              tone: "danger",
              values: { op: "boom" },
              onError: onActionError,
            }),
            h(Button, {
              onClick: () => setDirectorOpen(false),
              children: [t("visual.director_closed")],
            })
          )
        )
      )
    : null

  // ── P7.2：猫娘实时弹幕框（**常驻面板底部**，文档流、不悬浮）─────
  const danmakuCard = h(
    Card,
    { title: t("visual.danmaku_title") },
    h(
      Stack,
      { gap: 6 },
      h(Text, null, t("visual.danmaku_hint")),
      danmakuList.length === 0
        ? h(Text, null, state.stage === "idle" ? idleTaunt : t("visual.danmaku_empty"))
        : h(
            Stack,
            { gap: 4 },
            danmakuList
              .slice(-3)
              .reverse()
              .map((item) =>
                h(
                  "span",
                  { key: item.id, style: { fontSize: "13px", lineHeight: "1.6" } },
                  item.text || ""
                )
              )
          )
    )
  )

  // ── 局终：展示归档的结算报告 ─────────────────────────────────────
  if (state.stage === "finished" && state.settlement) {
    const report = state.settlement
    const catgirlResult = report.catgirl_result || null
    const scene = (report.death_scene || null) as DeathScene | null
    const achievements =
      (state.achievements || report.achievements || null) as Achievements | null
    // P8.1：结算页也要能「再来一局」。老版本插件没把 join 挂成 UI action 时
    // 就**不渲染**这个按钮 —— 绝不能拿一个空 action 去调。
    const joinAction = actionById("join")

    // ── P5.5 改动 4：天台虚惊一场（分三屏，第三屏是结算数据）──────
    if (scene && sceneScreen < 3) {
      const current =
        sceneScreen === 1 ? scene.screen1 : sceneScreen === 2 ? scene.screen2 : scene.screen3
      return (
        <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
          <Stack gap={12}>
            {h("style", null, RRM_CSS)}
            {titleBar}
            <Card title={t("panel.death_scene")}>
              <Stack gap={12}>
                {h("div", { style: { whiteSpace: "pre-wrap", lineHeight: "1.8" } }, current || "")}
                <Inline gap={8}>
                  <Button
                    onClick={() => {
                      setSceneScreen(3)
                    }}
                  >
                    {/* P8.1：嘲讽版「跳过」。 */}
                    {t("panel.death_scene_skip_mock")}
                  </Button>
                </Inline>
              </Stack>
            </Card>
            {danmakuCard}
          </Stack>
        </Page>
      )
    }

    // ── P5.5 改动 7：称号图鉴（第 12 区块）──────────────────────
    const rows: AchievementRow[] = []
    const unlockedList = (achievements && achievements.unlocked) || []
    const lockedList = (achievements && achievements.locked) || []
    for (const item of unlockedList) {
      rows.push({
        unlocked: true,
        title: item.title || "",
        text: item.epitaph || "",
      })
    }
    for (const item of lockedList) {
      rows.push({
        unlocked: false,
        title: item.title || "？？？",
        text: item.hint || "",
      })
    }

    // ── P8.1：结算页的三处「内部枚举不许见光」──────────────────────
    //   ① 标签用了带占位符的 "panel.mode"（"模式：{mode}"），而这里
    //      t() **没有传参** ⇒ 玩家看到的就是字面量「{mode}」。
    //      结算页的模式是**一行里的一个值**，所以标签单独给一个"模式"。
    //   ② 死因 / 结局 / 入场身份之前直接显示 starved / quick 这种英文枚举。
    const deadReport = Boolean(report.death_reason)
    const modeText = String(report.mode || "")
    const modeValue = modeText === "infinite"
      ? t("panel.mode_infinite")
      : modeText
        ? `${t("panel.mode_classic")} · ${report.mode_name || modeText}`
        : "—"

    return (
      <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          {/* P8.1：破产了就把标题换成嘲讽版（活下来也有话说）。 */}
          <Card
            title={
              deadReport ? t("panel.settlement_title_dead") : t("panel.settlement_title_alive")
            }
          >
            <Stack gap={12}>
              {/* P8.1：破产猫图放大 + 居中（内联 data URI）。
                  PNG 源图已提到 320×240，放大后依然清晰。 */}
              {h(
                "div",
                { style: { display: "flex", justifyContent: "center" } },
                h("img", {
                  src: MEOW_BANKRUPT,
                  alt: t("panel.settlement_image_alt"),
                  style: { width: "240px", height: "180px", borderRadius: "14px" },
                })
              )}
              <Alert tone="info" message={t("panel.settlement_notice")} />
              <KeyValue
                items={[
                  { key: "mode", label: t("panel.mode_label"), value: modeValue },
                  { key: "days", label: t("panel.days_survived"), value: report.days_survived },
                  { key: "trades", label: t("panel.trades"), value: report.trade_count },
                  {
                    key: "reason",
                    label: t("panel.death_reason"),
                    value: enumText(t, "death_reason", report.death_reason),
                  },
                  { key: "title", label: t("panel.death_title"), value: report.death_title || "—" },
                  {
                    key: "ending",
                    label: t("panel.ending"),
                    value: enumText(t, "ending", report.ending),
                  },
                  {
                    key: "identity",
                    label: t("panel.identity"),
                    value: enumText(t, "identity.label", report.identity),
                  },
                ]}
              />
              {/* P5.5 改动 2：身份呼应句 —— 只在死亡结算页出现 */}
              {report.identity_echo ? <Text>{report.identity_echo}</Text> : null}
              {/* P5.5 改动 1：「无为而治」的打工叙事 */}
              {report.inaction_worker ? <Text>{report.inaction_worker}</Text> : null}
              {/* 猫娘播报 */}
              {catgirlResult ? (
                <KeyValue
                  items={[
                    {
                      key: "catgirl_alive",
                      label: t("catgirl.alive"),
                      value: catgirlResult.alive ? t("catgirl.alive") : t("catgirl.dead"),
                    },
                    {
                      key: "catgirl_days",
                      label: t("catgirl.survived_days"),
                      value: catgirlResult.days_survived,
                    },
                    {
                      key: "catgirl_cash",
                      label: t("catgirl.cash"),
                      value: money(catgirlResult.final_cash),
                    },
                    {
                      key: "catgirl_trades",
                      label: t("panel.trades"),
                      value: catgirlResult.trade_count,
                    },
                    {
                      key: "catgirl_reason",
                      label: t("panel.death_reason"),
                      value: enumText(t, "death_reason", catgirlResult.death_reason),
                    },
                  ]}
                />
              ) : null}
              {/* P5.5 改动 3：猫娘沉默旁白（在猫娘播报之后） */}
              {report.catgirl_silence ? <Text>{report.catgirl_silence}</Text> : null}
              <Text>{report.anti_fraud_notice || ""}</Text>
              {/* P8.1：结算完别让玩家干瞪眼 —— 给一个**带嘲讽**的再来一局。 */}
              {joinAction ? (
                <Inline gap={8} align="center">
                  <ActionButton
                    action={joinAction}
                    label={t("panel.settlement_again")}
                    tone="primary"
                    values={{ mode: "classic_arcade" }}
                    onError={onActionError}
                  />
                </Inline>
              ) : null}
            </Stack>
          </Card>

          {/* ── P7 模块二 ④：Emoji 海结算（至少 50 个 🤡 / 🪦）── */}
          <Card title={t("visual.emoji_sea_title")}>
            <Stack gap={8}>
              <Text>{t("visual.emoji_sea_caption")}</Text>
              <Grid cols={10} gap={2}>
                {emojiSea.map((emoji, index) => (
                  <span key={index}>{emoji}</span>
                ))}
              </Grid>
            </Stack>
          </Card>

          {/* ⑫ 称号图鉴（P5.5 改动 7）：可折叠 */}
          {achievements ? (
            <Card title={t("panel.achievements")}>
              <Stack gap={8}>
                <Inline gap={8} align="center" wrap>
                  <StatusBadge
                    tone="default"
                    label={t("panel.achievements_count", {
                      unlocked: achievements.unlocked_count || 0,
                      total: achievements.total || 0,
                    })}
                  />
                  <Button
                    onClick={() => {
                      setAchievementsOpen(achievementsOpen === "1" ? "0" : "1")
                    }}
                  >
                    {achievementsOpen === "1"
                      ? t("panel.achievements_collapse")
                      : t("panel.achievements_expand")}
                  </Button>
                </Inline>
                {achievementsOpen === "1" ? (
                  <List<AchievementRow>
                    items={rows}
                    render={(row) => (
                      <Inline gap={8} align="center" wrap>
                        <StatusBadge
                          tone={row.unlocked ? "success" : "default"}
                          label={row.unlocked ? row.title : t("panel.achievements_locked")}
                        />
                        <Text>{row.text}</Text>
                      </Inline>
                    )}
                  />
                ) : null}
              </Stack>
            </Card>
          ) : null}
          {danmakuCard}
        </Stack>
      </Page>
    )
  }

  // ── 没开局 ───────────────────────────────────────────────────────
  if (state.stage !== "playing") {
    const joinAction = actionById("join")
    return (
      <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          {boomModal}
          <Card title={t("panel.idle_title")}>
            <Stack gap={12}>
              <Alert tone="info" message={state.notice || t("panel.idle")} />
              <Text>{t("panel.idle_hint")}</Text>
              {/* ── P7.4：真正的开局入口 ──────────────────────────────
                  0.8.3 事故：空闲态只有一行冰冷的文字，玩家**没法开局** ——
                  要么去聊天框打「加入一局」，要么就卡在这儿。这里给两个大按钮，
                  点一下直接开一局。

                  注意 ``joinAction`` 可能不存在（老版本插件没有把 join 挂成
                  UI action）—— 那就不渲染按钮，绝不能拿一个空 action 去调，
                  否则宿主会把它当成面板运行时错误上报。 */}
              {joinAction ? (
                <Inline gap={8} wrap={true} align="center">
                  <ActionButton
                    action={joinAction}
                    label={t("panel.join_classic")}
                    tone="primary"
                    values={{ mode: "classic_arcade" }}
                    onError={(error: any) => {
                      showToast(String((error && error.message) || error), "danger")
                      resync()
                    }}
                  />
                  <ActionButton
                    action={joinAction}
                    label={t("panel.join_infinite")}
                    tone="danger"
                    values={{ mode: "infinite" }}
                    onError={(error: any) => {
                      showToast(String((error && error.message) || error), "danger")
                      resync()
                    }}
                  />
                </Inline>
              ) : null}
              <Text>{t("panel.idle_modes_hint")}</Text>
            </Stack>
          </Card>
          {danmakuCard}
        </Stack>
      </Page>
    )
  }

  // ── P7 模块二 ②：荒诞弹窗（赛博网贷广告）─────────────────────
  // P7.3：**点按钮 = 同步关窗**，然后才异步调后端。
  //
  // 0.8.2 的死锁根因：这里用的是宿主 `ActionButton`，而它的 `onResult` 被
  // `await props.api.refresh()` 挡在后面 —— 刷新一失败，回调永远不来，弹窗就永远
  // 关不掉；无限模式下这个弹窗**又只有那一个按钮**，于是玩家被彻底锁死，
  // 连「宣布破产」都点不了（它被遮罩挡住了）。
  //
  // 现在有三道保险，任何一道都能把玩家放出来：
  //   ① 不用 `ActionButton`，直接宿主 `Button` + `props.api.call`，**点了立刻关**；
  //   ② **永远**给一个「我不借了，把我埋了吧」的逃生按钮；
  //   ③ 30 秒本地倒计时，到点自己关（服务端看门狗同时自动结算）。
  const loanChoice = (choice: string) => {
    // ── 同步关窗：不管后端成不成功，玩家都不会被卡住 ──
    setLoanModalOpen(false)
    try {
      props.api
        .call("loan_prompt", { choice }, { userInitiated: true })
        .then(async (result: any) => {
          await props.api.refresh()
          const note = result && result.note ? String(result.note) : ""
          if (note) {
            showToast(note, result && result.failed ? "danger" : "info")
          }
        })
        .catch((error: any) => {
          showToast(String((error && error.message) || error), "danger")
        })
    } catch (_error) {
      // 连调用都抛出来了：弹窗已经关了，至少玩家还能继续操作别的。
    }
  }

  const loanCanBorrow = state.mode === "infinite"

  const loanModal = h(Modal, {
    open: loanModalOpen,
    title: t("visual.loan_modal_title"),
    // 点遮罩 / 按 ESC 依然关不掉（这是广告的排面）——
    // 但**按钮永远在**，而且 30 秒后自动收工，玩家绝不会被困住。
    closeOnBackdrop: false,
    closeOnEscape: false,
    size: "md",
    children: h(
      Stack,
      { gap: 12 },
      // P8.0：弹窗配图（猫爪按着合同）—— 最基础的 <img>，不带 onError
      // 也无所谓：它是**内联 data URI**，不存在加载失败这回事。
      h("img", {
        src: MEOW_LOAN,
        alt: t("visual.loan_modal_image_alt"),
        style: { width: "160px", height: "120px", borderRadius: "12px" },
      }),
      h(Text, null, t("visual.loan_modal_body")),
      h(Alert, { tone: "danger", message: t("visual.loan_modal_note") }),
      // P7.3：把逃生通道**写在脸上**（玩家不用猜有没有出路）。
      h(Alert, { tone: "info", message: t("visual.loan_modal_escape_hint") }),
      // P7.3：倒计时（玩家看得见自己还剩多久就要被自动结算）。
      h(Alert, {
        tone: "warning",
        message: t("visual.loan_modal_countdown", { seconds: loanLeft }),
      })
    ),
    footer: h(
      "div",
      { className: "neko-button-group" },
      // ── 「借走 500 万」（经典模式没有高利贷，就不显示）─────────
      loanCanBorrow
        ? h(Button, {
            tone: "danger",
            onClick: () => {
              loanChoice("borrow")
            },
            children: t("visual.loan_modal_confirm"),
          })
        : null,
      // ── 逃生通道：**永远存在**，无限模式也有 ──────────────────
      h(Button, {
        tone: "default",
        onClick: () => {
          loanChoice("refuse")
        },
        children: t("visual.loan_modal_refuse"),
      })
    ),
  })

  return (
    <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Stack className={stageClass} gap={12}>
        {h("style", null, RRM_CSS)}
        {/* ── 0.8.1 病危表现：**只有**一条红色 Alert 警告条（文档流内）。
            绝不用闪烁 / 半透明 / 遮罩 —— 那是 0.8.0 崩 UI 的根因。───── */}
        {critical ? (
          <Alert
            tone="danger"
            message={`${visual.critical_emoji || "🩸"} ${t("visual.critical")}`}
          />
        ) : null}
        {titleBar}
        {directorConsole}
        {boomModal}

        {/* ── P7.2 ⓪：状态条（压缩成一行徽标，别占地方）────────────── */}
        <Inline gap={8} wrap align="center">
          <StatusBadge
            tone={PHASE_TONE[state.phase || ""] || "default"}
            label={t(`phase.${state.phase || "honeymoon"}`)}
          />
          <StatusBadge tone="default" label={t("panel.day", { day: state.day })} />
          <StatusBadge
            tone="default"
            label={t("panel.mode", { mode: state.mode_name || state.mode || "" })}
          />
          <StatusBadge
            tone="default"
            label={t("panel.to_settle", { ticks: state.ticks_to_settle })}
          />
          <StatusBadge tone="default" label={t("panel.tick", { tick: state.tick })} />
          <StatusBadge tone="default" label={stageLabel} />
          <Text>
            {t("panel.distance_to_kill")}:{" "}
            {state.distance_to_kill === null || state.distance_to_kill === undefined
              ? "—"
              : money(state.distance_to_kill)}
          </Text>
        </Inline>

        {/* ── P7.2 ①：K 线 —— 面板最上方的**主视觉**，占满全宽 ─────── */}
        <Card title={t("panel.kline")}>
          <Stack gap={8}>
            {stocks.length === 0 ? (
              <Text>{t("panel.kline_empty")}</Text>
            ) : (
              <>
                <Inline gap={8} wrap align="center" className="rrm-kline-row">
                  <Select
                    value={klineStockId}
                    options={stockOptions}
                    onChange={(value: any) => setSelectedStock(String(value))}
                  />
                  <ActionButton
                    action={actionById("kline")}
                    label={t("panel.kline_fetch")}
                    values={{ stock_id: klineStockId }}
                    onResult={(result: any) => {
                      if (result) setKlineDetail(result as Record<string, any>)
                    }}
                    onError={(error: any) =>
                      showToast(String((error && error.message) || error), "danger")
                    }
                  />
                  {klineStock ? (
                    <Text>
                      {t("panel.kline_hint", {
                        name: klineStock.display_name || klineStock.name,
                        price: money(klineStock.price),
                      })}
                    </Text>
                  ) : null}
                </Inline>
                <Kline
                  points={klinePoints}
                  emptyText={t("panel.kline_empty")}
                  warmingText={t("panel.kline_warming")}
                />
              </>
            )}
          </Stack>
        </Card>

        {/* ── P8.0 ②：家道三档的第一屏数字 ──────────────────────────
            顺序改成「盈亏 | 现钱 | **身家**」—— 身家那张卡落在**右上角**，
            它的名字随 stage 变：零花钱（富二代）/ 家底（断供）/ 棺材本（濒死）。 */}
        <Grid cols={3} gap={12}>
          <BigStat
            label={t("visual.pnl_title")}
            value={`${pnl > 0 ? "+" : ""}${money(pnl)}`}
            hint={`${t("visual.total_pnl")} · ${pnlHint}`}
            color={pnlColor}
            size={36}
          />
          <BigStat
            label={cashLabel}
            value={money(state.cash)}
            hint={stageHint}
            size={30}
          />
          <BigStat
            label={assetsLabel}
            value={money(totalAssets)}
            hint={t("visual.assets_hint", { stage: stageLabel })}
            color={flashColor}
            size={36}
          />
        </Grid>

        {/* ── P7.2 ③：明日账单（交不起就是红色警告）────────────────── */}
        {(state.bill_gap || 0) > 0 ? (
          <Alert
            tone="danger"
            message={t("panel.bill_warn", {
              gap: money(state.bill_gap),
              next: money(state.next_bill),
              cash: money(state.cash),
              missed: state.missed_bills || 0,
            })}
          />
        ) : (
          <Alert
            tone="success"
            message={t("panel.bill_ok", { next: money(state.next_bill), cash: money(state.cash) })}
          />
        )}

        {/* ── P8.0 ③：中半区 —— 行情**方块卡片网格** ──────────────────
            用宿主 `Columns` 的 fluid 模式：面板宽就是 4 列，窄就是 2 列。
            每张方块卡 = 股票名（+ 群友头像）+ 微型折线 + 巨大涨跌百分比。
            卡片是 `Columns` 的**直接**子元素 —— 中间没有任何 div 包裹层。 */}
        <Card title={t("visual.market_title")}>
          {stocks.length === 0 ? (
            <Text>{t("panel.no_stocks")}</Text>
          ) : (
            <Stack gap={10}>
              <Columns fluid minWidth={228} gap={12}>
                {visibleStocks.map((stock) => h(StockTile, { key: stock.id, stock, t }))}
              </Columns>
              {hiddenStockCount > 0 ? (
                <Text>{t("panel.more_stocks", { count: hiddenStockCount })}</Text>
              ) : null}
            </Stack>
          )}
        </Card>

        {/* ── P7.2 ⑤：持仓列表（只留名称 / 市值 / 大号盈亏）───────── */}
        <Card title={t("visual.holdings_title")}>
          <Stack gap={12}>
            {positions.length === 0 ? (
              <Text>{t("panel.no_positions")}</Text>
            ) : (
              <Columns fluid minWidth={196} gap={12}>
                {positions.map((position) =>
                  h(PositionTile, { key: position.stock_id, position, t })
                )}
              </Columns>
            )}
            {(state.concentration || 0) > 0.6 ? (
              <Alert
                tone="warning"
                message={t("visual.concentration_warn", {
                  value: percent(state.concentration),
                })}
              />
            ) : null}
          </Stack>
        </Card>

        {/* ── P8.0 ④：下半区 · 操作台 ────────────────────────────────
            「一键梭哈 / 一键割肉」升级成**胶囊状大按钮**（`rrm-pill`，
            flex:1 各占一半宽，带一点点薄荷绿 / 珊瑚粉微光）；
            辅助按钮（分析 / 领工资 / 借高利贷 / 拒绝 / 跳过本日）**缩小**
            （`rrm-mini`）放在大按钮下面。 */}
        <Card title={t("panel.trade")}>
          <Stack gap={12}>
            {/* ── P8.0 ④：两个胶囊大按钮**并排**独占一行 ──────────────
                真机教训（0.9.0 首轮）：它们原本和 `Select` 挤在同一行，
                `Select` 抢走了几乎全部宽度 ⇒ 两个大按钮被压成竖排细条、
                「一键割肉」还被卡片裁掉。
                真机教训（0.9.0 二轮）：改用 `Inline` 还是不均分 ——
                `ActionButton` 会把按钮包进 `<div class="neko-action-control">`，
                而那个壳是 `inline-grid`（收缩到内容宽），`flex: 1 1 0` 落不到
                真正的 flex 子项上。
                所以这里用 **`Grid cols={2}`** —— 网格列天生等分，不管子项
                是裸 `<button>` 还是带壳的 `ActionButton`。 */}
            <Grid cols={2} gap={8} className="rrm-trade-row">
              {buyAllAmount > 0 ? (
                <ActionButton
                  className="rrm-pill rrm-pill-buy"
                  action={actionById("buy")}
                  label={t("visual.buy_all")}
                  tone="primary"
                  values={{ stock_id: effectiveStockId, amount: buyAllAmount }}
                  onError={onActionError}
                />
              ) : (
                <Button
                  className="rrm-pill rrm-pill-buy"
                  onClick={() => {
                    showToast(t("visual.no_money"), "warning")
                  }}
                >
                  {t("visual.buy_all")}
                </Button>
              )}
              {sellAllAmount > 0 ? (
                <ActionButton
                  className="rrm-pill rrm-pill-sell"
                  action={actionById("sell")}
                  label={t("visual.sell_all")}
                  tone="danger"
                  values={{ stock_id: effectiveSellStockId, amount: sellAllAmount }}
                  onError={onActionError}
                />
              ) : (
                <Button
                  className="rrm-pill rrm-pill-sell"
                  onClick={() => {
                    showToast(t("visual.nothing_to_sell"), "warning")
                  }}
                >
                  {t("visual.sell_all")}
                </Button>
              )}
            </Grid>

            {/* ── 标的：买入 / 卖出各一行选择器（不跟大按钮抢宽度）──── */}
            <Inline gap={10} wrap={false} align="center" className="rrm-select-row">
              <Select
                value={effectiveStockId}
                options={stockOptions}
                onChange={(value: any) => setSelectedStock(String(value))}
              />
            </Inline>
            <Text>{t("visual.buy_all_hint")}</Text>

            <Inline gap={10} wrap={false} align="center" className="rrm-select-row">
              <Select
                value={effectiveSellStockId}
                options={positionOptions}
                onChange={(value: any) => setSellStock(String(value))}
              />
            </Inline>
            <Text>{t("visual.sell_all_hint")}</Text>

            <Divider />

            <Inline gap={8} wrap align="center">
              <ActionButton
                className="rrm-mini"
                action={actionById("analyze")}
                label={t("action.analyze")}
                values={{ stock_id: effectiveStockId }}
                onResult={(result: any) => {
                  if (result) setAnalysis(result as AnalysisResult)
                }}
                onError={onActionError}
              />
              <Text>{t("panel.analyses_left", { count: state.analyses_left || 0 })}</Text>
            </Inline>

            <Inline gap={8} wrap align="center">
              <ActionButton
                className="rrm-mini"
                action={actionById("daily_claim")}
                label={t("daily.claim")}
                onError={onActionError}
              />
              <ActionButton
                className="rrm-mini"
                action={actionById("daily_loan")}
                label={t("daily.loan")}
                tone="danger"
                onError={onActionError}
              />
              <ActionButton
                className="rrm-mini"
                action={actionById("daily_reject")}
                label={t("panel.reject")}
                onError={onActionError}
              />
              {/* P5.5 改动 6：跳过本日（无限模式专用，经典模式入口会报错） */}
              <ActionButton
                className="rrm-mini"
                action={actionById("skip_day")}
                label={t("panel.skip_day")}
                onError={onActionError}
              />
            </Inline>

            {analysis ? (
              <Card title={t("panel.analysis_result")}>
                <Stack gap={8}>
                  <Inline gap={8} wrap align="center">
                    <StatusBadge
                      tone={RATING_TONE[analysis.rating] || "default"}
                      label={t(`analysis.${analysis.rating}`)}
                    />
                    <Text>
                      {analysis.name} · {money(analysis.price)}
                    </Text>
                  </Inline>
                  <KeyValue
                    items={[
                      { key: "chasing", label: t("panel.chasing"), value: analysis.chasing ? t("common.yes") : t("common.no") },
                      { key: "weight", label: t("panel.weight"), value: percent(analysis.position_weight) },
                      { key: "left", label: t("panel.analyses_left", { count: analysis.analyses_left }) },
                    ]}
                  />
                  {analysis.market_note ? (
                    <Alert tone="warning" message={analysis.market_note} />
                  ) : null}
                  <Text>{analysis.note}</Text>
                </Stack>
              </Card>
            ) : null}
          </Stack>
        </Card>

        {/* ── P7.2 ⑦：社区名册（群友的专属标的）──────────────────── */}
        <Card title={t("visual.roster_title")}>
          {rosterList.length === 0 ? (
            <Text>{t("visual.roster_empty")}</Text>
          ) : (
            <Stack gap={8}>
              {rosterList.map((member) => (
                <Inline key={member.stock_id} gap={8} align="center" wrap>
                  <StatusBadge tone="default" label={member.alias || ""} />
                  <Text>{member.title || ""}</Text>
                  <Text>{money(member.price)}</Text>
                  <Text>{member.persona || ""}</Text>
                </Inline>
              ))}
            </Stack>
          )}
        </Card>

        {/* ── P7.2 ⑧：当天新闻（局中只有中性分类，标题正文要点开才给）─ */}
        <Card title={t("panel.news")}>
          <Stack gap={8}>
            {news.length === 0 ? (
              <Text>{t("panel.news_empty")}</Text>
            ) : (
              <List<NewsItem>
                items={news}
                render={(item) => (
                  <Inline gap={8} align="center" wrap>
                    <StatusBadge tone="default" label={item.kind_label} />
                    <Text>{t("panel.news_hint")}</Text>
                    <ActionButton
                      action={actionById("news_detail")}
                      label={t("panel.news_detail")}
                      values={{ news_id: item.id }}
                      onResult={(result: any) => {
                        if (result) setNewsDetail(result as Record<string, any>)
                      }}
                    />
                  </Inline>
                )}
              />
            )}
            {newsDetail ? (
              <Card title={t("panel.news_detail")}>
                <Stack gap={8}>
                  <Inline gap={8} align="center">
                    <StatusBadge tone="default" label={newsDetail.kind_label} />
                    <Text>{newsDetail.headline}</Text>
                  </Inline>
                  {newsDetail.roster_alias ? (
                    <Inline gap={8} align="center" wrap>
                      <StatusBadge tone="default" label={newsDetail.roster_alias} />
                      <Text>{newsDetail.roster_title || ""}</Text>
                    </Inline>
                  ) : null}
                  <Text>{newsDetail.content}</Text>
                  <Text>{t("news.truth_note")}</Text>
                </Stack>
              </Card>
            ) : null}
          </Stack>
        </Card>

        {/* ── ⑨ 猫娘公开状态 ─────────────────────────────────────── */}
        {catgirl ? (
          <Card title={t("panel.catgirl")}>
            <KeyValue
              items={[
                {
                  key: "alive",
                  label: t("catgirl.alive"),
                  value: catgirl.alive ? t("catgirl.alive") : t("catgirl.dead"),
                },
                { key: "cash", label: t("catgirl.cash"), value: money(catgirl.cash) },
                {
                  key: "holdings",
                  label: t("catgirl.holdings"),
                  value: t("catgirl.holdings_value", {
                    value: money(catgirl.positions_value),
                    count: catgirl.position_count,
                  }),
                },
                {
                  key: "days",
                  label: t("catgirl.survived_days"),
                  value: catgirl.days_survived,
                },
                { key: "action", label: t("panel.today_action"), value: catgirl.today_action },
              ]}
            />
          </Card>
        ) : null}

        {/* ── ⑩ 二道贩子弹窗（方案 A：引擎零改动，面板弹窗征求同意）─ */}
        {showPawnbroker && pending ? (
          <Card title={t("panel.pawnbroker")}>
            <Stack gap={8}>
              <Inline gap={8} align="center" wrap>
                <StatusBadge tone="danger" label={t("visual.force_liquid")} />
                <Text>
                  {t("panel.pawnbroker_hint", {
                    amount: pending.amount || 0,
                    reason: pending.reason || "",
                  })}
                </Text>
              </Inline>
              <Inline gap={8}>
                <ActionButton
                  action={actionById("daily_reject")}
                  label={t("panel.pawnbroker_reject")}
                  tone="danger"
                  onResult={() => setDismissedProposal(proposalKey)}
                />
                {/* 「接受」不需要调任何入口 —— 不撤销就是默认接受，
                    强制卖出照常执行（P5 契约 §3.4 方案 A：引擎零改动）。 */}
                <Button
                  onClick={() => {
                    setDismissedProposal(proposalKey)
                  }}
                >
                  {t("panel.pawnbroker_accept")}
                </Button>
              </Inline>
            </Stack>
          </Card>
        ) : null}

        {/* ── P7.2 ⑪：猫娘实时弹幕框（**永远在面板最后**）────────── */}
        {danmakuCard}
        {loanModal}
      </Stack>
    </Page>
  )
}
