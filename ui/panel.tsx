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
  Slider,
  Stack,
  StatusBadge,
  Step,
  Steps,
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

import { AVATARS, MEOW_LOAN } from "./assets/avatars"

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

/**
 * P10：节奏（游戏速度 + 自动/手动推进）。
 *
 * **纯表现**：这里只有"现实里等多久"的换算，一个游戏数值都没有。
 */
type Pace = {
  speed?: "slow" | "normal" | "fast"
  auto?: boolean
  base_interval_seconds?: number
  interval_seconds?: number
  ticks_per_day?: number
  day_seconds?: number
  multiplier?: number
}

/** P10：新手引导状态（已读标记记在 player_id 上）。 */
type Onboarding = {
  seen?: boolean
  auto_open?: boolean
  player_id?: string
}

/**
 * P11：猫娘主动搭话（外置交互引导）的状态。
 *
 * **纯表现**：这里只有"猫娘有没有开口"和"兜底弹窗有没有新消息"，
 * 一个游戏数值都没有 —— 主动搭话**不参与**任何游戏规则。
 */
type Meow = {
  /** 首次加载引导已经打过招呼了（全局一次）。 */
  greeted?: boolean
  /** 现在能不能呼叫（防连点冷却中就是 false）。 */
  can_call?: boolean
  /** 还要等几秒才能再呼叫。 */
  call_cooldown?: number
  /** 兜底弹窗的序号 —— 变了就说明"有一条新的猫娘消息"。 */
  fallback_seq?: number
  /** 兜底弹窗的台词（宿主接不住主动搭话时才有）。 */
  fallback_line?: string
  /** 兜底弹窗的原因（``push_failed`` 等）。 */
  fallback_reason?: string
}

/**
 * P12：手续费率（**只读** ``engine_config.fees``）。
 *
 * 面板要算"预计买入多少 / 预计花费多少 / 剩余现金"，用的是**引擎那一套
 * 费率**（``buy_fee`` / ``sell_fee``）—— 抄进载荷只是为了让面板**本地**
 * 算，拖滑块时零往返。**不改任何费率**（读，不是写）。
 */
type Fees = {
  buy_rate?: number
  sell_rate?: number
  stamp_tax_rate?: number
}

/**
 * P12：剧情状态（四幕叙事 + 外置 Yui）。
 *
 * **纯表现**：面板只显示"这一幕的文字"和（降级时的）选项按钮 ——
 * 猫娘在外置气泡里说，**面板不画立绘、不画对话框**。
 */
type StoryChoice = {
  id?: string
  label?: string
  hint?: string
}

type Story = {
  /** 每演一幕 +1 —— 面板靠它判断"这一幕是不是新的"。 */
  seq?: number
  /** 当前待选的章节 id（选完就清空）。 */
  pending?: string
  title?: string
  /** 纯文本场景（**不含任何立绘**）。 */
  scene?: string
  /** 聊天卡片投递成功了吗？失败时选项由面板接管。 */
  card_ok?: boolean
  /** 只有 ``card_ok === false`` 时才有内容。 */
  choices?: StoryChoice[]
  tags?: string[]
  debt?: { amount?: number; due_day?: number; paid?: number } | null
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
  /** P10：节奏（游戏速度 / 自动·手动）。 */
  pace?: Pace
  /** P10：新手引导（要不要自动弹《玩法说明》）。 */
  onboarding?: Onboarding
  /** P11：猫娘主动搭话的状态。 */
  meow?: Meow
  /** P12：手续费率（只读）。 */
  fees?: Fees
  /** P12：剧情状态。 */
  story?: Story
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
const TREND_UP = "#35BFA0"
const TREND_DOWN = "#F2677F"

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

/* ── ① 主题变量覆写（P9.0：轻盈透明的二次元科技风；P10 微调）──────────────
   色值取自设计图：背景 #EAF4FE → #F6FBFF，卡片 #F0F7FF（近白蓝），
   绿胶囊 #E7F8F0、粉胶囊 #FBE6EA，强调蓝 #5B8FF9，正文深蓝 #1F2D3D。
   毛玻璃用「半透明白 + 柔光投影」近似（宿主不支持背景模糊效果）。

   P10 微调（视觉任务三）：底色再**通透**一档 —— 顶部那片天蓝压得更透，
   中段快速褪成近白，卡片因此像是「浮」在浅蓝空气里而不是贴在色块上。 */
.rrm-root {
  --text: #1f2d3d;
  --muted: #7b8ca6;
  --primary: #5b8ff9;
  --success: #17916f;
  --danger: #d6486a;
  --border: rgba(142, 171, 219, 0.22);
  --surface: rgba(255, 255, 255, 0.85);
  --surface-strong: #ffffff;
  --bg: #e4efff;
  --radius-md: 20px;
  background: linear-gradient(180deg, #e4efff 0%, #f4faff 48%, #fbfdff 100%);
  color: var(--text);
}

/* ── ② 毛玻璃卡片：半透明白底 + 20px 大圆角 + **柔而大**的蓝影 ──────────
   P10 微调：阴影从「10px 30px / α0.15」改成「18px 46px / α0.15」——
   位移更大、模糊更大、透明度不变 ⇒ 影子更散、更软，卡片于是有「浮起来」的轻盈感。 */
.rrm-root .neko-card {
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 20px;
  box-shadow: 0 18px 46px rgba(133, 163, 214, 0.15);
}
.rrm-root .neko-card-header { padding: 16px 18px 0; }
.rrm-root .neko-card-body { padding: 14px 18px 18px; gap: 16px; }
.rrm-root .neko-card-title { color: #1f2d3d; font-size: 16px; font-weight: 700; letter-spacing: 0.5px; }
.rrm-root .neko-heading { color: #1f2d3d; }
.rrm-root .neko-text { color: #6b7a90; }

/* ── ③ 页面标题：24px 加粗深蓝（设计图的标题规格）────────────────── */
.rrm-root .neko-page { gap: 16px; }
.rrm-root .neko-page-title { font-size: 24px; font-weight: 700; color: #1f2d3d; letter-spacing: 0.5px; }
.rrm-root .neko-page-subtitle { color: #a8bacd; font-size: 13px; }

/* ── ④ 状态徽标 = 浅蓝胶囊 Tag（像游戏里的标签）───────────────────── */
.rrm-root .neko-badge {
  background: #e4efff;
  border: 1px solid rgba(91, 143, 249, 0.16);
  color: #5b8ff9;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}
.rrm-root .neko-badge[data-tone="success"] { background: #e7f8f0; color: #17916f; border-color: rgba(23, 145, 111, 0.24); }
.rrm-root .neko-badge[data-tone="warning"] { background: #fff3de; color: #b3760f; border-color: rgba(240, 178, 74, 0.28); }
.rrm-root .neko-badge[data-tone="danger"] { background: #fde9ed; color: #d6486a; border-color: rgba(214, 72, 106, 0.26); }

/* ── ⑤ 大数字统计块（赚亏 / 现钱 / 身家）────────────────────────── */
.rrm-root .neko-stat {
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.65);
  border-radius: 18px;
  box-shadow: 0 14px 36px rgba(133, 163, 214, 0.14);
}
.rrm-root .neko-stat-label { color: #9fb2c6; font-size: 12px; }

/* ── ⑥ 行情方块卡：两层结构（名字 + 涨跌 / 走势 + 现价）──────────── */
.rrm-root .neko-card.rrm-tile {
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 18px;
  box-shadow: 0 14px 34px rgba(133, 163, 214, 0.15);
  padding: 12px 14px;
}
.rrm-root .neko-card.rrm-tile .neko-card-body { padding: 0; gap: 8px; }
.rrm-root .rrm-tile-head { display: flex; align-items: center; gap: 8px; width: 100%; }
.rrm-root .rrm-tile-foot { display: flex; align-items: center; gap: 10px; width: 100%; }
/* 迷你走势要**撑满**卡片宽度 —— 否则走势和现价之间会空一大片。 */
.rrm-root canvas.rrm-spark,
.rrm-root div.rrm-spark {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  height: 30px;
}
.rrm-root .rrm-stock-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  font-weight: 700;
  font-size: 13px;
  color: #3d5575;
}
.rrm-root .rrm-tile-name-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rrm-root .rrm-price {
  margin-left: auto;
  font-size: 15px;
  font-weight: 700;
  color: #3a5478;
  white-space: nowrap;
}
.rrm-root .rrm-change-pill {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 16px;
  font-weight: 800;
  line-height: 1.4;
  white-space: nowrap;
}
.rrm-root .rrm-change-up { background: #e7f8f0; color: #17916f; }
.rrm-root .rrm-change-down { background: #fde9ed; color: #d6486a; }
.rrm-root .rrm-change-flat { background: #eef3fa; color: #7b8ca6; }

/* ── ⑦ K 线：柱子带纵向渐变（设计图的「光影」），底下垫一层网格 ─────── */
.rrm-root .rrm-kline-wrap {
  background-image: repeating-linear-gradient(180deg, rgba(142, 171, 219, 0.10) 0px, rgba(142, 171, 219, 0.10) 1px, rgba(255, 255, 255, 0) 1px, rgba(255, 255, 255, 0) 40px);
}
.rrm-root .rrm-kline-bar-up { background: linear-gradient(180deg, #6fd8bd 0%, rgba(90, 200, 168, 0.22) 100%); }
.rrm-root .rrm-kline-bar-down { background: linear-gradient(180deg, #ff8fa4 0%, rgba(255, 107, 129, 0.22) 100%); }

/* ── ⑧ 操作台的两个大胶囊按钮：渐变质感 + 56px 高 + 18px 粗体 ─────── */
.rrm-root .neko-button.rrm-pill {
  flex: 1 1 0;
  min-width: 0;
  height: 56px;
  padding: 0 16px;
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 1px;
  border-radius: 28px;
  border: none;
  /* 真机教训：按钮被压窄时中文会**竖着排**（一字一行），难看且读不出来。
     锁死不换行，宁可两行空间也要横着排。 */
  white-space: nowrap;
}
.rrm-root .neko-button.rrm-pill-buy {
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  color: #16305c;
  box-shadow: 0 10px 24px rgba(123, 188, 255, 0.45);
}
.rrm-root .neko-button.rrm-pill-sell {
  background: linear-gradient(90deg, #ffc5d3 0%, #ff96b2 100%);
  color: #8a1e3c;
  box-shadow: 0 10px 24px rgba(255, 150, 178, 0.45);
}
/* 按钮下面那行小字（设计图里的 All In / Stop Loss 副标题） */
.rrm-root .rrm-pill-captions { display: flex; gap: 8px; width: 100%; }
.rrm-root .rrm-pill-caption {
  width: 48%;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.rrm-root .rrm-pill-caption-buy { color: #2f6fbf; }
.rrm-root .rrm-pill-caption-sell { color: #d2496e; }

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

/* ── P12：仓位控制（滑块 + 百分比快捷键）+ 实时预览 ──────────────────
   滑块是宿主**原生**的 input[type=range]（.neko-slider）—— 我们只调宽度，
   **不加任何定位、层叠、滤镜、透明**（0.8.0 的崩溃红线一条没破）。
   百分比快捷键是四个普通按钮，选中的那个上高亮色。 */
.rrm-root .neko-slider { width: 100%; }
.rrm-root .neko-grid.rrm-trade-pct {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
}
.rrm-root .neko-button.rrm-pct {
  height: 34px;
  padding: 0 6px;
  font-size: 13px;
  font-weight: 700;
  border-radius: 17px;
  white-space: nowrap;
}
.rrm-root .neko-button.rrm-pct-on {
  background: linear-gradient(180deg, #a4e5ff 0%, #7bbcff 100%);
  color: #16305c;
  border: none;
}
.rrm-root .neko-grid.rrm-trade-previews {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.rrm-root .neko-grid.rrm-trade-previews .neko-text {
  font-size: 12px;
  line-height: 1.5;
  color: #7b8ca6;
}

/* ── P12：剧情沉浸模式（宿主原生 Modal size="full"）─────────────────
   宿主 Modal 的遮罩本来就是固定定位铺满整屏（**宿主自己的**），
   所以全面板覆盖**零危险 CSS**。这里只有**纯文本场景** ——
   猫娘在外置气泡里说，面板不画立绘、不画对话框。

   下面这两条只改**高度**（宿主那个 full 尺寸只撑宽、不撑高）：
   把弹窗拉满 92vh，body 自己滚动 —— 这就是"沉浸模式"该有的样子。
   （只用了 display / flex / height，**没有**定位、层叠、滤镜、透明。） */
.rrm-root .neko-modal[data-size="full"] {
  height: 92vh;
  display: flex;
  flex-direction: column;
}
.rrm-root .neko-modal[data-size="full"] .neko-modal-body {
  flex: 1 1 auto;
  max-height: none;
}
.rrm-root .rrm-story-scene {
  white-space: pre-wrap;
  font-size: 16px;
  line-height: 2;
}
.rrm-root .rrm-story-choices { display: grid; gap: 8px; }
.rrm-root .rrm-story-fallback-hint {
  font-size: 12px;
  color: #7b8ca6;
}
.rrm-root .neko-button.rrm-story-choice {
  width: 100%;
  height: 48px;
  padding: 0 14px;
  font-size: 15px;
  font-weight: 700;
  text-align: left;
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

/* ── ⑨ 浅蓝胶囊 Tag（标题栏那一行）──────────────────────────────── */
.rrm-root .rrm-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 999px;
  background: #e4efff;
  border: 1px solid rgba(91, 143, 249, 0.16);
  color: #5b8ff9;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

/* ── ⑩ 破产配图：CSS 拼的扁平空钱包（不再用那张黄色土豆图）────────── */
.rrm-root .rrm-wallet-scene {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 4px 0 2px;
}
.rrm-root .rrm-wallet {
  display: flex;
  align-items: stretch;
  width: 200px;
  height: 124px;
  border-radius: 18px;
  overflow: hidden;
  background: linear-gradient(180deg, #eaf2ff 0%, #cfdefb 100%);
  border: 1px solid rgba(255, 255, 255, 0.75);
  box-shadow: 0 12px 28px rgba(142, 171, 219, 0.28);
}
.rrm-root .rrm-wallet-body {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.rrm-root .rrm-wallet-slot {
  width: 62px;
  height: 5px;
  border-radius: 999px;
  background: rgba(142, 171, 219, 0.35);
}
.rrm-root .rrm-wallet-tear { font-size: 26px; line-height: 1; }
.rrm-root .rrm-wallet-flap {
  width: 58px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #9cc0f5 0%, #7ba8f0 100%);
  border-left: 1px solid rgba(255, 255, 255, 0.55);
}
.rrm-root .rrm-wallet-clasp {
  width: 14px;
  height: 14px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 2px 6px rgba(60, 96, 160, 0.35);
}
.rrm-root .rrm-wallet-spark { font-size: 42px; line-height: 1; }

/* ── ⑪ 辅助按钮：缩小、变轻 ─────────────────────────────────────── */
.rrm-root .neko-button.rrm-mini {
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 650;
  border-radius: 999px;
}

/* ── ⑫ 群友头像（方块卡里的小圆头像）──────────────────────────── */
.rrm-root .rrm-avatar {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.9);
  vertical-align: middle;
  flex: 0 0 auto;
}

/* ── ⑬ P7.2 遗留的三档字体（只改字体，不改布局）──────────────────── */
.rrm-font-rich { font-family: "YouYuan", "Yuanti SC", "Hiragino Maru Gothic ProN", "Microsoft YaHei UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
.rrm-font-broke { font-weight: 300; }
.rrm-font-dying { font-weight: 500; }
.rrm-root .neko-button.rrm-escape { font-size: 11px; padding: 2px 8px; color: var(--muted); border-radius: 999px; }

/* ── ⑭ P10：玩法说明大按钮（空闲态最显眼的那一个）─────────────────────
   「怎么玩」必须比「开局」还要醒目 —— 新手第一眼看到的就是它。 */
.rrm-root .neko-button.rrm-guide-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: 100%;
  min-height: 62px;
  padding: 12px 22px;
  border-radius: 31px;
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  color: #16305c;
  box-shadow: 0 16px 38px rgba(123, 188, 255, 0.42);
}
.rrm-root .rrm-guide-btn-main { font-size: 18px; font-weight: 800; letter-spacing: 1px; }
.rrm-root .rrm-guide-btn-sub { font-size: 12px; font-weight: 600; letter-spacing: 0.3px; }
/* 标题栏里那个小链接（局中随时翻说明）。 */
.rrm-root .neko-button.rrm-guide-link {
  font-size: 11px;
  padding: 3px 11px;
  border-radius: 999px;
  color: var(--muted);
  font-weight: 650;
}

/* ── ⑯ P11：呼叫猫娘 + 拟态外置对话 ─────────────────────────────────── */
/* 标题栏里的【呼叫猫娘】小按钮（比《玩法说明》链接显眼一点点）。 */
.rrm-root .neko-button.rrm-meow-call {
  font-size: 11px;
  padding: 3px 11px;
  border-radius: 999px;
  font-weight: 700;
  color: #2b6ca8;
  background: rgba(164, 229, 255, 0.42);
  border: 1px solid rgba(123, 188, 255, 0.5);
}
/* 兜底弹窗里那条"消息"：左头像 + 右气泡。 */
.rrm-root .rrm-meow-thread {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.rrm-root .rrm-meow-avatar {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  line-height: 1;
  background: linear-gradient(180deg, #d8f0ff 0%, #bfe2ff 100%);
  border: 1px solid rgba(123, 188, 255, 0.45);
}
.rrm-root .rrm-meow-bubble {
  flex: 1 1 auto;
  padding: 11px 14px;
  border-radius: 16px;
  background: #eaf6ff;
  border: 1px solid rgba(123, 188, 255, 0.38);
  color: #1f2d3d;
  font-size: 13px;
  line-height: 1.6;
  box-shadow: 0 8px 22px rgba(133, 163, 214, 0.14);
}

/* ── ⑮ P10：玩法说明弹窗里的三步图文 ──────────────────────────────── */
/* P11.1：三步的**骨架**已经换回宿主的原生 Steps / Step 组件
   （neko-step / neko-step-index / neko-step-title 由宿主 styles.css 自己
   样式化），所以这里只剩下两行**正文**排版。
   注意：这段 CSS 整体是一个反引号模板字符串，注释里**绝对不能出现反引号**。 */
.rrm-guide-step-body { font-size: 13px; line-height: 1.8; color: #5b6b82; }
.rrm-guide-step-tip { font-size: 12px; line-height: 1.7; color: #8ba0bd; }
.rrm-guide-footer {
  font-size: 12px;
  color: #8ba0bd;
  text-align: center;
  letter-spacing: 0.5px;
}

/* ── ⑯ P10：节奏卡（游戏速度 + 自动/手动）─────────────────────────── */
.rrm-pace-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rrm-pace-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 13px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1px;
}
.rrm-pace-badge-auto {
  background: #e7f8f0;
  border: 1px solid rgba(23, 145, 111, 0.2);
  color: #17916f;
}
.rrm-pace-badge-manual {
  background: #fdf0e4;
  border: 1px solid rgba(185, 112, 42, 0.22);
  color: #b9702a;
}
.rrm-pace-hint { font-size: 13px; color: #5b6b82; }
.rrm-root .neko-button.rrm-speed-tag {
  width: 100%;
  padding: 9px 8px;
  border-radius: 999px;
  border: 1px solid rgba(142, 171, 219, 0.3);
  background: rgba(255, 255, 255, 0.78);
  color: #6b7a90;
  font-size: 13px;
  font-weight: 750;
  letter-spacing: 0.5px;
}
.rrm-root .neko-button.rrm-speed-tag-on {
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  border-color: rgba(255, 255, 255, 0.78);
  color: #16305c;
  box-shadow: 0 10px 24px rgba(123, 188, 255, 0.38);
}
.rrm-pace-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #8ba0bd;
}
.rrm-pace-actions { display: flex; gap: 10px; align-items: center; }
.rrm-root .neko-button.rrm-pace-toggle {
  flex: 0 0 auto;
  padding: 9px 18px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 750;
}
.rrm-root .neko-button.rrm-pace-advance {
  flex: 1 1 auto;
  min-height: 52px;
  border-radius: 26px;
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  color: #16305c;
  font-size: 17px;
  font-weight: 800;
  letter-spacing: 1px;
  box-shadow: 0 14px 32px rgba(123, 188, 255, 0.42);
}
.rrm-pace-note { font-size: 12px; line-height: 1.7; color: #8ba0bd; }
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

/**
 * 涨绿跌红（P9.0）。
 *
 * 用的是设计图那一档**柔和**的薄荷绿 / 珊瑚粉 —— 它们要落在浅色胶囊底上，
 * 不能用纯饱和色（浅绿底 + 亮绿字会糊成一片）。
 */
function changeColor(value: number | undefined | null): string {
  const pct = Number(value || 0) * 100
  if (pct > 0.001) return "#17916f"
  if (pct < -0.001) return "#d6486a"
  return "#8ba0bd"
}

/** 把 #RRGGBB 变成 rgba(r,g,b,a) —— canvas 画渐变面积时要用。 */
function hexAlpha(hex: string, alpha: number): string {
  const value = String(hex || "").replace("#", "")
  if (value.length !== 6) return hex
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
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
function Sparkline(props: {
  points: number[]
  up: boolean
  title?: string
  width?: number
  height?: number
}) {
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
      const pad = 3
      const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0
      const color = props.up ? TREND_UP : TREND_DOWN
      if (values.length < 2) {
        // 刚开盘只有一两个点 —— 画一条**两端淡出的平直基线**。
        // （孤零零一个小点看着像渲染坏了，不像「还没数据」。）
        const base = ctx.createLinearGradient(0, 0, width, 0)
        base.addColorStop(0, hexAlpha(color, 0))
        base.addColorStop(0.5, hexAlpha(color, 0.55))
        base.addColorStop(1, hexAlpha(color, 0))
        ctx.strokeStyle = base
        ctx.lineWidth = 2
        ctx.lineCap = "round"
        ctx.beginPath()
        ctx.moveTo(1, height / 2)
        ctx.lineTo(width - 1, height / 2)
        ctx.stroke()
        return
      }
      const coords = values.map((value, index) => ({
        x: pad + step * index,
        y: height - pad - ((value - min) / span) * (height - pad * 2),
      }))
      // P9.0：折线底下垫一层**渐变面积** —— 设计图里那条「会发光的走势带」。
      try {
        const area = ctx.createLinearGradient(0, 0, 0, height)
        area.addColorStop(0, hexAlpha(color, 0.34))
        area.addColorStop(1, hexAlpha(color, 0))
        ctx.beginPath()
        ctx.moveTo(coords[0].x, height)
        coords.forEach((point) => ctx.lineTo(point.x, point.y))
        ctx.lineTo(coords[coords.length - 1].x, height)
        ctx.closePath()
        ctx.fillStyle = area
        ctx.fill()
      } catch (error) {
        // 老浏览器没有渐变 API —— 少一层面积而已，不影响读数。
      }
      ctx.strokeStyle = color
      ctx.lineWidth = 1.8
      ctx.lineJoin = "round"
      ctx.lineCap = "round"
      ctx.beginPath()
      coords.forEach((point, index) => {
        if (index === 0) ctx.moveTo(point.x, point.y)
        else ctx.lineTo(point.x, point.y)
      })
      ctx.stroke()
      // 末端点一个小圆点（设计图的折线末端有个实心点）。
      const tail = coords[coords.length - 1]
      ctx.beginPath()
      ctx.arc(tail.x, tail.y, 2, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()
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
        className: "rrm-spark",
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
    className: "rrm-spark",
    width: props.width || 84,
    height: props.height || 26,
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
  const change = Number(stock.change || 0)
  const tone = change > 0.001 ? "up" : change < -0.001 ? "down" : "flat"
  // P9.0：卡片改成**设计图的两层结构** ——
  //   上层：股票名（+ 群友头像）  ····  右侧是涨跌胶囊
  //   下层：迷你走势（带渐变面积）  ····  右侧是现价
  return h(
    Card,
    { className: "rrm-tile" },
    h(
      "div",
      { className: "rrm-tile-head" },
      h(
        "span",
        { className: "rrm-stock-name" },
        h(RosterAvatar, { alias: stock.roster_alias }),
        h(
          "span",
          { className: "rrm-tile-name-text" },
          stock.display_name || stock.name
        )
      ),
      h(
        "span",
        {
          className: `rrm-change-pill rrm-change-${tone}`,
          title: t("visual.change_title"),
          style: { color: changeColor(stock.change) },
        },
        changeText(stock.change)
      )
    ),
    h(
      "div",
      { className: "rrm-tile-foot" },
      h(Sparkline, {
        points: (stock.spark || []).map((point) => point[1]),
        up,
        title: t("visual.trend_title"),
        width: 200,
        height: 30,
      }),
      h(
        "span",
        { className: "rrm-price", title: t("visual.price_title") },
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
  const tone = pnl > 0.001 ? "up" : pnl < -0.001 ? "down" : "flat"
  // P9.0：和行情卡同一套两层结构（名字 + 盈亏胶囊 / 市值）。
  return h(
    Card,
    { className: "rrm-tile" },
    h(
      "div",
      { className: "rrm-tile-head" },
      h(
        "span",
        { className: "rrm-stock-name" },
        h("span", { className: "rrm-tile-name-text" }, position.display_name || position.name)
      ),
      h(
        "span",
        {
          className: `rrm-change-pill rrm-change-${tone}`,
          title: t("visual.pnl_title"),
          style: { color: changeColor(pnl) },
        },
        `${pnl > 0 ? "+" : ""}${money(pnl)}`
      )
    ),
    h(
      "div",
      { className: "rrm-tile-foot" },
      h("span", { className: "rrm-price" }, money(position.market_value))
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
  const bars = points.map((point, index) => {
    const tick = point[0]
    const price = point[1]
    const barHeight = Math.max(2, Math.round(((price - min) / span) * (height - 16)))
    const previous = index > 0 ? points[index - 1][1] : price
    // P9.0：柱子改成**纵向渐变**（顶上实、底下化开），底下再垫一层网格线 ——
    // 这就是设计图里那条 K 线区的「光影」。
    return h("div", {
      key: tick,
      title: `tick ${tick} · ${price.toFixed(4)}`,
      className: price >= previous ? "rrm-kline-bar-up" : "rrm-kline-bar-down",
      style: {
        flex: "1 1 0",
        minWidth: "3px",
        maxWidth: "16px",
        height: `${barHeight}px`,
        marginRight: "2px",
        borderRadius: "3px",
      },
    })
  })
  return h(
    "div",
    {
      className: "rrm-kline-wrap",
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

/**
 * P9.0：结算页的**扁平空钱包**插画 —— 纯 CSS 拼的，不依赖任何素材文件。
 *
 * 0.9.1 那张「破产猫猫」是 PIL 画的：一只黄色猫瘫在灰色钱包上，远看就是个
 * 土豆，玩家直呼辣眼睛。这次彻底删掉那张图，改成设计图同款的扁平线性风格：
 * 浅蓝渐变的钱包身 + 深蓝搭扣 + 一滴泪 + 飞出去的钞票。
 *
 * 没用 ``position`` / ``filter``，纯 flex 布局 ⇒ 不会踩 0.8.0 那四个雷。
 */
function BankruptArt(props: { alt: string }) {
  return h(
    "div",
    {
      className: "rrm-wallet-scene",
      role: "img",
      title: props.alt,
      "aria-label": props.alt,
    },
    h("span", { className: "rrm-wallet-spark" }, "💸"),
    h(
      "div",
      { className: "rrm-wallet" },
      h(
        "div",
        { className: "rrm-wallet-body" },
        h("div", { className: "rrm-wallet-slot" }),
        h("div", { className: "rrm-wallet-tear" }, "💧")
      ),
      h(
        "div",
        { className: "rrm-wallet-flap" },
        h("div", { className: "rrm-wallet-clasp" })
      )
    )
  )
}

/**
 * P10：玩法说明弹窗（三步图文）。
 *
 * 用**宿主自带的** ``Modal`` + ``Alert`` + ``Steps`` / ``Step``
 * （官方推荐做法之一）；宿主组件几乎不透传 ``style``，所以视觉一律走
 * ``className`` + 注入的 ``RRM_CSS``。
 *
 * **P11.1：用回原生的 ``Steps`` / ``Step``** —— 之前那三步是手搓的
 * ``.rrm-guide-step`` / ``.rrm-guide-num`` / ``.rrm-guide-step-title``，
 * 而宿主 UI Kit 里本来就有 ``Steps``（竖排步骤条）和 ``Step``
 * （序号 + 标题 + 正文），渲染出来的结构一模一样
 * （``neko-step`` / ``neko-step-index`` / ``neko-step-title``）。
 * 换回原生组件 ⇒ 少一堆自定义 CSS，也更像"官方原生样式"。
 *
 * 三步分别讲：① 零花钱/家底/棺材本 + 每日生活费；② 一键梭哈/一键割肉 + 看涨跌图；
 * ③ 猫娘会实时嘴臭、接受不了别玩。
 */
function GuideModal(props: {
  open: boolean
  t: Translate
  onClose: () => void
}) {
  const { open, t, onClose } = props
  const step = (index: number, title: string, body: string, tip: string) =>
    h(
      Step,
      { index: index, title: title, key: title },
      h("div", { className: "rrm-guide-step-body" }, body),
      tip ? h("div", { className: "rrm-guide-step-tip" }, tip) : null
    )
  return h(Modal, {
    open: open,
    title: t("guide.modal_title"),
    onClose: onClose,
    closeOnBackdrop: true,
    closeOnEscape: true,
    size: "lg",
    children: h(
      Stack,
      { gap: 12 },
      h(
        Steps,
        null,
        step(1, t("guide.step1_title"), t("guide.step1_body"), t("guide.step1_tip")),
        step(2, t("guide.step2_title"), t("guide.step2_body"), t("guide.step2_tip")),
        step(3, t("guide.step3_title"), t("guide.step3_body"), "")
      ),
      h(Alert, { tone: "warning", message: t("guide.step3_alert") }),
      h("div", { className: "rrm-guide-footer" }, t("guide.footer"))
    ),
    footer: h(
      "div",
      { className: "neko-button-group" },
      h(Button, {
        tone: "primary",
        onClick: onClose,
        children: t("guide.ack"),
      })
    ),
  })
}

/**
 * P11：拟态外置对话弹窗（**兜底**）。
 *
 * **这是降级路径，不是主路径。**
 *
 * 主路径是宿主 SDK 的 ``push_message(ai_behavior="respond")``：插件把一段提示
 * 喂给 LLM 并立刻触发一个 AI 回合，**外置猫娘**（桌宠）自己开口说话。
 *
 * 只有当宿主**接不住**这次主动搭话（老版本宿主 / 推送被拒）时，才会退到这
 * 个弹窗 —— 用"猫娘发来一条微信消息"的样式，在**面板内部**演一段台词。
 * 所以弹窗里必须写清楚这是模拟，不能让玩家以为桌宠真的没说话。
 *
 * 视觉一律走 ``className`` + 注入的 ``RRM_CSS``（宿主组件几乎不透传 style）。
 */
function MeowChatModal(props: {
  open: boolean
  t: Translate
  line: string
  reason: string
  onClose: () => void
}) {
  const { open, t, line, reason, onClose } = props
  return h(
    Modal,
    {
      open: open,
      title: t("meow.modal_title"),
      onClose: onClose,
      size: "sm",
      children: h(
        Stack,
        { gap: 12 },
        h(
          "div",
          { className: "rrm-meow-thread" },
          h("span", { className: "rrm-meow-avatar" }, "🐱"),
          h("div", { className: "rrm-meow-bubble" }, line || t("meow.fallback_empty"))
        ),
        h(Alert, {
          tone: "info",
          message: reason === "push_failed" ? t("meow.fallback_push") : t("meow.fallback_generic"),
        })
      ),
      footer: h(
        "div",
        { className: "neko-button-group" },
        h(Button, {
          tone: "primary",
          onClick: onClose,
          children: t("meow.ack"),
        })
      ),
    }
  )
}

/**
 * P10：节奏卡（游戏速度 + 自动/手动推进）。
 *
 * **只改"现实里等多久"**，一个游戏数值都不碰：
 *   * 速度三档（慢 / 标准 / 快）＝ 心跳间隔的倍率；
 *   * 自动 / 手动 ＝ 心跳要不要自己往前跳；
 *   * 手动模式下才出现【推进一拍】—— 自动模式里给这个按钮会让"自动"变成谎话。
 */
function PaceControl(props: {
  pace: Pace
  t: Translate
  ticksToSettle?: number
  /** P11.1：有 action 在飞 ⇒ 按钮禁用 + 显示"推进中"。 */
  busy?: boolean
  onSpeed: (speed: string) => void
  onToggleAuto: () => void
  onAdvance: () => void
}) {
  const { pace, t, ticksToSettle, busy, onSpeed, onToggleAuto, onAdvance } = props
  const speed = pace.speed || "normal"
  const auto = pace.auto !== false
  const interval = pace.interval_seconds || 0
  const minutes = Math.max(1, Math.round((pace.day_seconds || 0) / 60))
  const speeds = [
    { id: "slow", label: t("panel.pace_speed_slow") },
    { id: "normal", label: t("panel.pace_speed_normal") },
    { id: "fast", label: t("panel.pace_speed_fast") },
  ]
  return h(
    Card,
    { title: t("panel.pace_title") },
    h(
      Stack,
      { gap: 12 },
      h(
        "div",
        { className: "rrm-pace-head" },
        h(
          "span",
          {
            className: auto
              ? "rrm-pace-badge rrm-pace-badge-auto"
              : "rrm-pace-badge rrm-pace-badge-manual",
          },
          auto ? t("panel.pace_auto_badge") : t("panel.pace_manual_badge")
        ),
        h(
          "span",
          { className: "rrm-pace-hint" },
          auto
            ? interval > 0
              ? t("panel.pace_auto_hint", { seconds: interval })
              : t("panel.pace_auto_hint_unknown")
            : t("panel.pace_manual_hint")
        )
      ),
      h(
        Grid,
        { cols: 3, gap: 8 },
        speeds.map((item) =>
          h(Button, {
            key: item.id,
            className:
              item.id === speed
                ? "rrm-speed-tag rrm-speed-tag-on"
                : "rrm-speed-tag",
            disabled: busy,
            onClick: () => onSpeed(item.id),
            children: item.label,
          })
        )
      ),
      h(
        "div",
        { className: "rrm-pace-meta" },
        h(
          "span",
          null,
          minutes > 0
            ? t("panel.pace_day_estimate", { minutes: minutes })
            : t("panel.pace_day_estimate_unknown")
        ),
        ticksToSettle === undefined || ticksToSettle === null
          ? null
          : h(
              "span",
              null,
              t("panel.pace_to_settle", { ticks: ticksToSettle })
            )
      ),
      h(
        "div",
        { className: "rrm-pace-actions" },
        h(Button, {
          className: "rrm-pace-toggle",
          tone: "default",
          disabled: busy,
          onClick: onToggleAuto,
          children: auto
            ? t("panel.pace_switch_manual")
            : t("panel.pace_switch_auto"),
        }),
        auto
          ? null
          : h(Button, {
              className: "rrm-pace-advance",
              // ── P11.1：请求在飞时禁用 + 显示"推进中" ──────────────
              // 狂点会被这条禁用态挡掉 ⇒ 宿主不会再收到并发的 action ⇒
              // 不会再有 `Failed to query plugin UI action context`。
              disabled: busy,
              onClick: onAdvance,
              children: busy ? t("panel.pace_advance_busy") : t("panel.pace_advance"),
            })
      ),
      h(
        "div",
        { className: "rrm-pace-note" },
        auto ? t("panel.pace_auto_note") : t("panel.pace_manual_note")
      )
    )
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

  // ── P10：节奏（游戏速度 / 自动·手动）与新手引导 ────────────────────
  // 宿主偶尔会返回**空状态**（宿主自己忙，5 秒预算用光）。这时候宁可显示
  // 上一次知道的节奏，也不要闪回"标准 0 秒/拍"吓人 —— 所以留个兜底缓存。
  const paceRef = useRef<Pace>({})
  const paceRaw = (state.pace || {}) as Pace
  const pace: Pace =
    Object.keys(paceRaw).length > 0 ? paceRaw : paceRef.current
  if (Object.keys(paceRaw).length > 0) {
    paceRef.current = paceRaw
  }
  const onboarding = state.onboarding || {}
  const [guideOpen, setGuideOpen] = useState(false)
  // 「只自动弹一次」：**本次挂载**只试一次（面板每 3 秒轮询，不能反复弹）。
  const guideAutoTried = useRef(false)
  useEffect(() => {
    if (guideAutoTried.current) return
    if (!onboarding.auto_open) return
    guideAutoTried.current = true
    setGuideOpen(true)
  }, [onboarding.auto_open])

  // ── P11：猫娘外置交互引导（主动搭话）───────────────────────────
  //
  // **主路径**：插件调 `push_message(ai_behavior="respond")`，宿主让**外置猫娘**
  // 真的开口。面板这一侧只做两件事：
  //   ① 挂载时调一次 `meow_greet`（首次加载引导，全局只说一次）；
  //   ② 盯住 `meow.fallback_seq` —— 序号变了就说明"宿主接不住，弹兜底对话"。
  const meow = (state.meow || {}) as Meow
  const [meowFallback, setMeowFallback] = useState<{ line: string; reason: string } | null>(
    null
  )
  // 「只招呼一次」：**本次挂载**只试一次。
  //
  // `userInitiated: false` 让宿主在插件还没起来时**自动重试**这次调用 ——
  // 玩家刚打开面板时插件可能正在启动，等它跑起来再补一句招呼。
  const meowGreetTried = useRef(false)
  useEffect(() => {
    if (meowGreetTried.current) return
    meowGreetTried.current = true
    try {
      const pending = props.api.call("meow_greet", {}, { userInitiated: false })
      if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
    } catch (_error) {
      // 呼不上就算了：绝不能因为一句引导语把面板搞崩。
    }
  }, [props.api])
  // 兜底弹窗：序号变了 ⇒ 有一条新的猫娘消息（宿主接不住主动搭话）。
  const meowFallbackSeq = useRef(-1)
  useEffect(() => {
    const seq = Number(meow.fallback_seq || 0)
    if (!seq || seq === meowFallbackSeq.current) return
    meowFallbackSeq.current = seq
    setMeowFallback({
      line: String(meow.fallback_line || ""),
      reason: String(meow.fallback_reason || ""),
    })
  }, [meow.fallback_seq, meow.fallback_line, meow.fallback_reason])

  // ── P12：剧情沉浸模式（宿主原生 ``Modal size="full"``）──────────────
  //
  // **猫娘在外置气泡里说，面板这里只放纯文本场景** —— 面板**不画立绘、
  // 不画对话框**（外置已经有 Yui 了）。
  //
  // 正常路径：**选项按钮在聊天卡片上**（贴着 Yui 气泡），面板只展示这一幕
  // 的场景文字；只有**卡片投递失败**时，面板才接管选项按钮（优雅降级）。
  const story = state.story || {}
  const [storyOpen, setStoryOpen] = useState(false)
  // 用**章节 id**做键（而不是 ``seq``）—— ``seq`` 是内存计数，插件重启就归零，
  // 那样"重启前就开着、还没选完"的那一幕就再也弹不出来了。
  const storyPendingRef = useRef("")
  useEffect(() => {
    const pending = String(story.pending || "")
    if (!pending) {
      // 这一幕演完了（玩家选完）⇒ 收起，并允许下一幕再弹。
      storyPendingRef.current = ""
      setStoryOpen(false)
      return
    }
    if (pending === storyPendingRef.current) return
    storyPendingRef.current = pending
    setStoryOpen(true)
  }, [story.pending, story.seq])

  // ── P11.1：全局请求锁 ────────────────────────────────────────────
  //
  // 宿主执行任何 UI action **之前**都会先 `get_ui_context()`（**5 秒预算**），
  // 而插件是**单进程单事件循环**：并发请求会让插件里的 `threading.Lock`
  // 抢不到就**阻塞整个事件循环**，宿主那次上下文查询随之超时 ⇒
  // 红色 `Failed to query plugin UI action context`（500）。
  //
  // 所以面板侧**只允许一个 action 在飞**：点下去立刻置 busy，
  // 等接口返回 **且** 面板刷新完，才允许下一次点击。
  const busyRef = useRef(false)
  const [busy, setBusy] = useState(false)

  /**
   * 走锁的 action 调用。
   *
   * * 已经有请求在飞 ⇒ **静默丢弃**（只弹一条温和的 info 提示，
   *   **绝不**红色警告制造恐慌）；
   * * 返回 action 的载荷（``Ok({...})`` 里的字典），拿不到就 ``null``。
   */
  const callAction = async (
    action: string,
    args: Record<string, any>,
    options?: { userInitiated?: boolean }
  ): Promise<any> => {
    if (busyRef.current) {
      showToast(t("panel.action_busy"), "info")
      return null
    }
    busyRef.current = true
    setBusy(true)
    try {
      const result = await props.api.call(action, args, {
        userInitiated: options?.userInitiated !== false,
      })
      await props.api.refresh()
      return result
    } catch (error: any) {
      showToast(String((error && error.message) || error), "danger")
      // 失败**也要**把上下文拉回来 —— 面板不能停在旧状态
      // （比如这一局已经结束了，屏幕上的按钮全是死的）。见 P7.4 的 resync。
      try {
        await props.api.refresh()
      } catch (_error) {
        // 拉不到就算了。
      }
      return null
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  /** 【呼叫猫娘】：玩家按一下，外置猫娘回一句。 */
  const callMeow = async () => {
    const result = await callAction("meow_call", {})
    if (result === null) return
    const spoken = Boolean(result.spoken)
    showToast(
      spoken ? t("panel.meow_called") : t("panel.meow_fallback_toast"),
      spoken ? "info" : "warning"
    )
  }

  /**
   * 【执行买入】：按滑块定的仓位买入。
   *
   * 走 ``callAction`` ⇒ **纳入 P11.1 的全局请求锁**（狂点只会被静默丢弃，
   * 不会并发打爆宿主那次 ``get_ui_context``）。
   */
  const executeBuy = async () => {
    if (buyAmount <= 0) {
      showToast(t("visual.no_money"), "warning")
      return
    }
    await callAction("buy", { stock_id: effectiveStockId, amount: buyAmount })
  }

  /** 【执行卖出】：按滑块定的仓位卖出。 */
  const executeSell = async () => {
    if (sellAmount <= 0) {
      showToast(t("visual.nothing_to_sell"), "warning")
      return
    }
    await callAction("sell", { stock_id: effectiveSellStockId, amount: sellAmount })
  }
  /** 关掉《玩法说明》＝"玩家看过了"：把已读标记记回 player_id。 */
  const closeGuide = () => {
    setGuideOpen(false)
    try {
      const pending = props.api.call("onboarding_ack", {}, { userInitiated: true })
      if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
    } catch (_error) {
      // 记不上就记不上：弹窗已经关了，绝不能因此挡住玩家。
    }
  }
  /**
   * 调一个 P10 的偏好入口（写完立刻刷新上下文）。
   *
   * **P11.1**：走全局请求锁；后端命中节流时（``throttled``）只给一条温和提示。
   */
  const callPace = async (action: string, args: Record<string, any>) => {
    const result = await callAction(action, args)
    if (result && result.throttled) {
      showToast(t("panel.action_throttled"), "info")
    }
  }

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

  // ── P12：仓位控制（滑块 + 百分比快捷键）+ 实时预览 ────────────────
  //
  // **为什么要把"一键梭哈"改掉**：全有或全无只有两个选项，玩家没有
  // "投一半"这个真实存在的动作 —— 而"投一半"恰恰是反诈教育里最该
  // 练的一手。
  //
  // **预览怎么算准**：手续费率是**引擎那一套**（``state.fees``，只读），
  // 所以"预计花费"和插件真正扣的钱**一致**（不是 0.995 那种经验折算）。
  const [positionPct, setPositionPct] = useLocalState<number>("rrm.position_pct", 100)
  const pct = Math.max(0, Math.min(100, Number(positionPct || 0)))
  const buyRate = Number(state.fees?.buy_rate || 0)
  const sellRate = Number(state.fees?.sell_rate || 0)
  const stampRate = Number(state.fees?.stamp_tax_rate || 0)
  const cash = Math.max(0, Number(state.cash || 0))

  const selectedStockRow = stocks.find((stock) => stock.id === effectiveStockId)
  const buyPrice = selectedStockRow ? Number(selectedStockRow.price || 0) : 0
  // 买入：预算 = 现金 × 仓位；按 ``notional × (1 + buy_rate) <= 预算`` 倒推数量。
  const buyBudget = (cash * pct) / 100
  const buyNotional = buyRate > -1 ? buyBudget / (1 + buyRate) : 0
  const buyAmount = buyPrice > 0 ? Math.floor(buyNotional / buyPrice) : 0
  const buyCost = buyAmount * buyPrice * (1 + buyRate)
  const buyCashAfter = Math.max(0, cash - buyCost)

  const selectedPosition = positions.find((item) => item.stock_id === effectiveSellStockId)
  const sellAllAmount = selectedPosition ? Number(selectedPosition.sellable || 0) : 0
  const sellPrice = selectedPosition ? Number(selectedPosition.price || 0) : 0
  // 卖出：回款 = 数量 × 价 × (1 - 卖出手续费 - 印花税)（印花税单边收）。
  const sellAmount = Math.floor((sellAllAmount * pct) / 100)
  const sellIncome = sellAmount * sellPrice * (1 - sellRate - stampRate)
  const sellCashAfter = cash + sellIncome

  // ── P7.2：一键梭哈 / 一键割肉的数量 ─────────────────────────────
  const buyAllAmount = buyAmount

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
    h("span", { className: "rrm-tag" }, `${t("panel.title")} · ${stageLabel}`),
    // ── P10：自动/手动徽标 —— 一眼看出市场是自己走，还是等玩家推一拍 ────
    h(
      "span",
      {
        className:
          pace.auto === false
            ? "rrm-pace-badge rrm-pace-badge-manual"
            : "rrm-pace-badge rrm-pace-badge-auto",
      },
      pace.auto === false
        ? t("panel.pace_manual_badge")
        : t("panel.pace_auto_badge")
    ),
    // ── P10：局中也能随时翻《玩法说明》（不用回空闲态）─────────────────
    h(Button, {
      className: "rrm-guide-link",
      tone: "default",
      onClick: (event: any) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation()
        }
        setGuideOpen(true)
      },
      children: t("panel.guide_button"),
    }),
    // ── P11：【呼叫猫娘】—— 让**外置**猫娘（桌宠）开口说话 ────────────
    // 空闲态与局中都在标题栏里：一个位置，两个状态都够得着。
    h(Button, {
      className: "rrm-meow-call",
      tone: "default",
      // P11.1：有 action 在飞时禁用（全局请求锁）。
      disabled: busy,
      onClick: (event: any) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation()
        }
        callMeow()
      },
      children: t("panel.meow_call"),
    }),
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

  // ── P10：节奏卡（游戏速度 + 自动/手动）─────────────────────────────
  const paceCard = h(PaceControl, {
    pace: pace,
    t: t,
    ticksToSettle: state.stage === "playing" ? state.ticks_to_settle : undefined,
    // ── P11.1：把全局请求锁的 busy 态传下去（按钮禁用 + "推进中"）──
    busy: busy,
    onSpeed: (next: string) => callPace("set_game_speed", { speed: next }),
    onToggleAuto: () => callPace("set_auto_tick", { enabled: pace.auto === false }),
    onAdvance: () => callPace("advance_tick", {}),
  })

  // ── P10：《玩法说明》弹窗（三步图文）───────────────────────────────
  const guideModal = h(GuideModal, {
    open: guideOpen,
    t: t,
    onClose: closeGuide,
  })

  // ── P11：拟态外置对话弹窗（**兜底**）──────────────────────────────
  // 只有宿主接不住主动搭话时才会出现（``meowFallback`` 非空即弹）。
  const meowModal = h(MeowChatModal, {
    open: meowFallback !== null,
    t: t,
    line: meowFallback ? meowFallback.line : "",
    reason: meowFallback ? meowFallback.reason : "",
    onClose: () => setMeowFallback(null),
  })

  /** P12：选了某个剧情选项（**只有卡片投递失败时面板才会接管**）。 */
  const chooseStory = async (choiceId: string) => {
    const result = await callAction("story_choice", {
      chapter_id: String(story.pending || ""),
      choice_id: choiceId,
    })
    if (result === null) return
    showToast(t("panel.story_choice_done"), "info")
  }

  // ── P12：剧情沉浸模式（宿主原生 ``Modal size="full"``）──────────────
  //
  // **为什么不用自己写全屏**：宿主原生 ``Modal`` 的遮罩本来就是
  // ``position: fixed; inset: 0`` ⇒ 它**天然**铺满整个面板视口。
  // 我们**一个危险 CSS 都不用写**（``position`` / ``inset`` / ``z-index``
  // 全是宿主自己的）。
  //
  // **面板里画什么**：只有**纯文本场景**。猫娘在外置气泡里说，
  // 面板**不画立绘、不画对话框**；正常路径的选项按钮在**聊天卡片**上，
  // 只有卡片投递失败时这里才接管选项（优雅降级）。
  const storyChoices = (story.choices || []) as StoryChoice[]
  const storyCardOk = story.card_ok !== false
  const storyDebt = story.debt || null
  const storyModal = h(
    Modal,
    {
      open: storyOpen,
      title: String(story.title || t("panel.story_modal_title")),
      onClose: () => setStoryOpen(false),
      size: "full",
      children: h(
        Stack,
        { gap: 12 },
        h("div", { className: "rrm-story-scene" }, String(story.scene || "")),
        storyDebt
          ? h(Alert, {
              tone: "warning",
              message: t("panel.story_debt", {
                amount: Number(storyDebt.amount || 0).toFixed(2),
                days: Math.max(
                  0,
                  Number(storyDebt.due_day || 0) - Number(state.day || 0)
                ),
              }),
            })
          : null,
        storyCardOk
          ? h(Alert, { tone: "info", message: t("panel.story_modal_hint") })
          : h(
              "div",
              { className: "rrm-story-choices" },
              h("div", { className: "rrm-story-fallback-hint" }, t("panel.story_choose_here")),
              storyChoices.map((choice) =>
                h(
                  Button,
                  {
                    key: String(choice.id || ""),
                    className: "rrm-story-choice",
                    disabled: busy,
                    onClick: () => {
                      void chooseStory(String(choice.id || ""))
                    },
                  },
                  String(choice.label || "")
                )
              )
            )
      ),
    }
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
            {guideModal}
            {meowModal}
            {storyModal}
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
              {/* P9.0：结算页配图换成**纯 CSS 拼的扁平空钱包**。
                  0.9.1 那张「破产猫」是 PIL 画的黄色土豆，看着像廉价表情包 ——
                  这次直接废掉这张图（素材文件也一并删掉），改成设计图同款的
                  扁平线性风格钱包。 */}
              {h(BankruptArt, { alt: t("panel.settlement_image_alt") })}
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
          {guideModal}
          {meowModal}
          {storyModal}
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
              {/* ── P10：大号《玩法说明》按钮 ──────────────────────────
                  上手门槛太高是这一版最致命的问题：玩家打开面板完全不知道
                  该干嘛。所以「怎么玩」必须比「开局」还醒目 —— 一个占满
                  整行的大胶囊按钮，点了弹出三步图文说明。 */}
              <Button
                className="rrm-guide-btn"
                tone="primary"
                onClick={() => {
                  setGuideOpen(true)
                }}
              >
                {[
                  h(
                    "span",
                    { className: "rrm-guide-btn-main", key: "main" },
                    t("panel.guide_button")
                  ),
                  h(
                    "span",
                    { className: "rrm-guide-btn-sub", key: "sub" },
                    t("panel.guide_button_sub")
                  ),
                ]}
              </Button>
            </Stack>
          </Card>
          {paceCard}
          {guideModal}
          {meowModal}
          {storyModal}
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
      <Stack className={stageClass} gap={16}>
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
        {guideModal}
        {meowModal}
        {storyModal}

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

        {/* ── P10：节奏卡（游戏速度 + 自动/手动）─────────────────────
            放在状态条正下方：自动/手动必须**一眼可见** —— 玩家抱怨的
            "Tick 快得让人窒息"，靠的就是这一张卡来解。 */}
        {paceCard}

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
              <Columns fluid minWidth={228} gap={16}>
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
              <Columns fluid minWidth={196} gap={16}>
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

        {/* ── P12：操作台 · 仓位控制 + 预览 + 执行 ───────────────────
            「一键梭哈 / 一键割肉」两个极端被换成：
            **仓位滑块 + 百分比快捷键 + 实时预览 + 【执行买入/执行卖出】**。

            * 滑块是宿主**原生** ``Slider``（``input[type=range]``）——
              零自绘、零危险 CSS；
            * 预览用**引擎那一套费率**（``state.fees``），所以"预计花费"
              和真扣的钱**一致**；
            * 执行按钮走 ``callAction`` ⇒ **纳入 P11.1 的全局请求锁**
              （``ActionButton`` 不支持 ``disabled``，也进不了这把锁）。 */}
        <Card title={t("panel.trade")}>
          <Stack gap={12}>
            {/* ── 仓位：滑块 + 百分比快捷键 ─────────────────────────── */}
            <Stack gap={6}>
              <Inline gap={8} align="center" justify="space-between">
                <Text>{t("panel.trade_position")}</Text>
                <Text>{t("panel.trade_pct", { pct: pct })}</Text>
              </Inline>
              <Slider
                min={0}
                max={100}
                step={5}
                value={pct}
                showValue={false}
                onChange={(value: any) => {
                  setPositionPct(Number(value))
                }}
              />
              <Grid cols={4} gap={6} className="rrm-trade-pct">
                {[25, 50, 75, 100].map((option) =>
                  h(Button, {
                    key: option,
                    className: pct === option ? "rrm-pct rrm-pct-on" : "rrm-pct",
                    disabled: busy,
                    onClick: () => {
                      setPositionPct(option)
                    },
                    children: t("panel.trade_pct", { pct: option }),
                  })
                )}
              </Grid>
              <Text>{t("panel.trade_position_hint")}</Text>
            </Stack>

            {/* ── P8.0 ④：两个胶囊大按钮**并排**独占一行 ──────────────
                真机教训（0.9.0 首轮）：它们原本和 `Select` 挤在同一行，
                `Select` 抢走了几乎全部宽度 ⇒ 两个大按钮被压成竖排细条、
                「一键割肉」还被卡片裁掉。
                真机教训（0.9.0 二轮）：改用 `Inline` 还是不均分 ——
                `ActionButton` 会把按钮包进 `<div class="neko-action-control">`，
                而那个壳是 `inline-grid`（收缩到内容宽），`flex: 1 1 0` 落不到
                真正的 flex 子项上。
                所以这里用 **`Grid cols={2}`** —— 网格列天生等分，不管子项
                是裸 `<button>` 还是带壳的 `ActionButton`。

                P12：两个按钮从 `ActionButton` 换成 **`Button` + `callAction`** ——
                `ActionButton` 既不支持 `disabled`，也进不了 P11.1 的全局
                请求锁（狂点会绕过"点击即禁用"）。 */}
            <Grid cols={2} gap={8} className="rrm-trade-row">
              <Button
                className="rrm-pill rrm-pill-buy"
                disabled={busy}
                onClick={() => {
                  void executeBuy()
                }}
              >
                {t("panel.trade_execute_buy")}
              </Button>
              <Button
                className="rrm-pill rrm-pill-sell"
                disabled={busy}
                onClick={() => {
                  void executeSell()
                }}
              >
                {t("panel.trade_execute_sell")}
              </Button>
            </Grid>

            {/* ── P9.0：两个大按钮下面各有一行小字（主副标题的层次）────
                P12：文案跟着按钮一起改了 ——「All In / Stop Loss」不再是事实
                （现在是按仓位买），所以换成「按上面的仓位买 / 卖」。 */}
            <Inline gap={8} className="rrm-pill-captions">
              <span className="rrm-pill-caption rrm-pill-caption-buy">
                {t("panel.trade_execute_buy_sub")}
              </span>
              <span className="rrm-pill-caption rrm-pill-caption-sell">
                {t("panel.trade_execute_sell_sub")}
              </span>
            </Inline>

            {/* ── 实时预览（两列，跟上面的按钮对齐）────────────────── */}
            <Grid cols={2} gap={8} className="rrm-trade-previews">
              <Text>
                {t("panel.trade_buy_preview", {
                  amount: buyAmount,
                  cost: buyCost.toFixed(2),
                  cash: buyCashAfter.toFixed(2),
                })}
              </Text>
              <Text>
                {t("panel.trade_sell_preview", {
                  amount: sellAmount,
                  income: sellIncome.toFixed(2),
                  cash: sellCashAfter.toFixed(2),
                })}
              </Text>
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
