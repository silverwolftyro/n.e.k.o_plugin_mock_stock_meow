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
  DataTable,
  ErrorBoundary,
  Field,
  Grid,
  Inline,
  Input,
  KeyValue,
  List,
  Modal,
  Page,
  Select,
  ScrollArea,
  Slider,
  Split,
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
  useScrollIntoView,
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
  /**
   * P23.2：波动率（**只读**）。
   *
   * 引擎算出来的（``stock.sigma``，整局不变），面板只拿它**排序**：
   * 方差最大的「妖股」排前面，稳定 / 中性的排后面 ——
   * 直播时观众第一眼看到的必须是**最容易出节目效果**的那几只。
   */
  sigma?: number
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
  /** P23.2：波动率（只读，见 ``Stock.sigma``）—— 持仓卡同一套排序。 */
  sigma?: number
}

/**
 * P23.2：「高关注度」排序（**前排高关注区 / 后排常规区**）。
 *
 * 直播看板的第一诉求：**观众第一眼看到的必须是最容易出节目效果的票**。
 * 所以排序键是三层，从粗到细：
 *
 *   ① **爆炸标签**（``explosive``）→ 2 分；中性 1 分；稳定 0 分。
 *      引擎的 ``classify_sigma`` 已经把波动率分成三档了，直接用。
 *   ② **波动率** ``sigma`` 越大越前 —— 同是爆炸票，波动更大的更靠前。
 *   ③ **当天涨跌幅的绝对值** —— 波动率相近时，今天动得最凶的先上。
 *
 * 最后用 ``id`` 兜底 ⇒ 排序是**确定的**（同样的载荷每次渲染顺序一致，
 * 不会因为 JS 排序不稳定而让方块在刷新时跳来跳去）。
 */
const TAG_HOTNESS: Record<string, number> = {
  explosive: 2,
  neutral: 1,
  stable: 0,
}

/** 标的的「热度分」：标签权重 + 波动率 + 当天振幅。 */
function stockHotness(stock: Stock): number {
  const tag = TAG_HOTNESS[String(stock.tag || "")] ?? 1
  const sigma = Number(stock.sigma || 0)
  const swing = Math.abs(Number(stock.change || 0))
  // 标签权重最大（×100），波动率次之（×10），当天振幅垫底 —— 层级不会串。
  return tag * 100 + sigma * 10 + swing
}

/** 行情方块：妖股在前、稳定在后（同热度时按 id 定序）。 */
function sortStocksByHotness(list: Stock[]): Stock[] {
  return list.slice().sort((a, b) => {
    const diff = stockHotness(b) - stockHotness(a)
    if (Math.abs(diff) > 1e-9) return diff
    return String(a.id || "").localeCompare(String(b.id || ""))
  })
}

/** 持仓方块：同一套热度分（同热度时市值大的在前，再按 id 定序）。 */
function sortPositionsByHotness(list: Position[]): Position[] {
  return list.slice().sort((a, b) => {
    const diff = stockHotness({
      tag: a.tag,
      sigma: a.sigma,
      change: 0,
    } as Stock) - stockHotness({
      tag: b.tag,
      sigma: b.sigma,
      change: 0,
    } as Stock)
    if (Math.abs(diff) > 1e-9) return -diff
    const value = Number(b.market_value || 0) - Number(a.market_value || 0)
    if (Math.abs(value) > 1e-9) return value
    return String(a.stock_id || "").localeCompare(String(b.stock_id || ""))
  })
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

/**
 * P26.0：群友随机事件（**白名单**载荷）。
 *
 * 只有公开字段：分类、标题、明细、金额、一枚 buff 标签。
 * 内参的**正文不在这里** —— 它只走 `push_message` 给 Yui，面板物理上看不到。
 */
type CommunityEvent = {
  id: string
  kind: string
  kind_label: string
  title: string
  detail: string
  day: number
  amount: number
  buff_tag?: string | null
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
  /**
   * P20.0：玩家自报的名字（**戏名**，不是宿主 session 里的真名）。
   *
   * 空闲态用它回填输入框；局中与结算页用它显示"这是谁"。
   */
  player_name?: string
  /**
   * P20.0：命中的名字彩蛋。
   *
   * **只有** `key` / `category` / `label_key` —— 没有倍率、没有数值。
   * `label_key` 指向 i18n 里的名号文案（面板自己查表显示徽标）。
   */
  name_egg?: { key?: string; category?: string; label_key?: string } | null
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
  /** P26.0：群友随机事件（天降 Token / 免费内参）。 */
  community_events?: CommunityEvent[]
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
  /** P16.0：连击（**纯展示**；不含任何游戏数值）。 */
  combo?: { streak?: number; best?: number; enabled?: boolean }
  /**
   * P18.0：动态教程。
   *
   * **只有功能性 UI 文案的 i18n key** —— 猫娘的台词**不在这里**
   * （它在面板外的外置气泡里，由 AI 生成）。
   */
  tutorial?: {
    active?: boolean
    phase?: string
    tier?: string
    index?: number
    total?: number
    waiting_gate?: boolean
    gate_wait_key?: string
    step?: { key?: string; anchor?: string; title_key?: string; body_key?: string }
  }
  /**
   * P19.0：导演段的「进度」（``[director] show_progress``）。
   *
   * **这个键只在开关打开时才会出现在载荷里** —— 开关关闭时是
   * ``undefined``，面板也**完全不渲染**。
   *
   * ⚠️ 这里的 ``hype`` 就是引擎局中隐藏的那个量。面板**不许**说出
   * 它的引擎字段名（P5 契约 §4.1 的静态 lint：面板源码里不许出现
   * ``heat`` / ``seed`` / ``reversal``），所以后端换了键名 ——
   * 面板只是显示一个后端决定给的数字，**闸门在后端**。
   */
  director_progress?: {
    hype?: number
    chapter?: number
    chapter_total?: number
    chapter_title?: string
    seen?: number
  }
  /**
   * P23.0：Yui 的**操控状态**（模式 / 授权 / 配额 / 最近一次介入）。
   *
   * **纯表现层** —— 面板只负责把"猫娘刚刚动了手"这件事显眼地画出来。
   */
  yui?: YuiBlock
  /** P27.0：做空账本（**白名单**字段）。 */
  short_positions?: ShortPositionRow[]
  /**
   * P27.0：做空控件的**选项**。
   *
   * 倍率表 / 上限 / 最小保证金一律由后端给 —— 面板**不硬编码**这些数字。
   */
  short_options?: {
    leverages?: number[]
    default_leverage?: number
    max_positions?: number
    min_margin?: number
  }
  /**
   * P27.0：玩家 vs 内置猫娘 Yui 的资产曲线（**纯表现层**）。
   *
   * 两条**等长**的数组；Yui 那条是合成数据，不来自引擎、不影响任何判定。
   */
  equity_curve?: { player?: number[]; yui?: number[] }
  /** P27.0：「报纸」在这一档白拿吗（新手前两档白拿）。 */
  report_free?: boolean
  /** P27.0：「报纸」的单价（元）。 */
  report_price?: number
}

/** P27.0：做空持仓的一行（后端给的**白名单**字段）。 */
type ShortPositionRow = {
  stock_id?: string
  margin?: number
  leverage?: number
  entry_price?: number
  price?: number
  pnl?: number
  margin_left?: number
  liquidated?: boolean
}

/**
 * P27.0：**专属结局卡片**（目前只有 Wehos 的开发猫结局）。
 *
 * 后端命中条件时才给这个字段；没有它就走普通的结算流程。
 */
type SpecialEnding = {
  kind?: string
  emoji?: string
  title?: string
  body?: string
}

/** P23.0：Yui 操控状态块。 */
type YuiBlock = {
  /** live（直播托管）/ assist（辅助式）/ spectate（纯看戏）。 */
  mode?: string
  /** P24.0：话痨级别 —— quiet（安静）/ normal（正常）/ chatty（话痨）。 */
  talk_level?: string
  /** P24.0：当前档位下**每隔几个 tick** 开一次回合（0 = 不主动开）。 */
  talk_interval?: number
  /** 辅助模式下玩家是否**当场**授权过。 */
  granted?: boolean
  grant_seconds?: number
  quota_left?: number
  quota_total?: number
  /** 介入**序号** —— 面板靠它判断"这条是不是新的"。 */
  seq?: number
  /** 最近一次介入的摘要。 */
  last?: {
    seq?: number
    action?: string
    stock_id?: string
    stock_name?: string
    ratio?: number
    amount?: number
    price?: number
    cash_after?: number
    at?: number
  } | null
}

/** P5.5 改动 4：天桥虚惊一场的三屏。 */
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
 * 涨红跌绿 —— P23.0 改成**中文炒股直觉**（红涨绿跌）。
 *
 * P8.0 原本是欧美 / 加密配色（涨绿跌红）：P23.0 按用户拍板**全局翻面**，
 * 与国内交易终端一致。
 *
 * 这两个值和 RRM_CSS 里的 ``--up`` / ``--down`` 是**同一套**：
 * canvas 画不出来 CSS 变量（canvas 只认字面色值），所以这里写死同样的
 * 十六进制，两边必须同步改。
 *
 * ⚠ 与 ``--danger``（#d6486a）/ ``--success``（#17916f）是**两套语义**：
 * 危险色表示「危钱 / 濒危 / 报错」，涨跌色只表示「价格方向」。
 * 两者碰巧同色系是刻意的 —— 中文终端里「红」本来就一词多义。
 */
const TREND_UP = "#F2677F"
const TREND_DOWN = "#35BFA0"

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
  /* P23.0：涨跌色 —— **红涨绿跌**（中文炒股直觉）。
     与 --danger / --success 是**两套语义**：危险色表示「危钱 / 濒危 / 报错」，
     涨跌色只表示「价格方向」。canvas 那边写死同色（见 TREND_UP / TREND_DOWN）。 */
  --up: #f2677f;
  --down: #35bfa0;
  /* P21.0：全屏沉浸 —— 底色**叠两层**：
     ① 顶部一层很淡的蓝晕（180deg，往下化开）；
     ② 原来的主渐变（浅蓝 → 近白）。
     两层都只用纵向渐变（斜向的那种 0.8.0 炸过一次），
     background-size: cover 让它在大屏下铺满而不是被拉扁。 */
  background:
    linear-gradient(180deg, rgba(91, 143, 249, 0.12) 0%, rgba(91, 143, 249, 0) 38%),
    linear-gradient(180deg, #e4efff 0%, #f4faff 48%, #fbfdff 100%);
  background-size: cover;
  background-repeat: no-repeat;
  color: var(--text);
  /* P13：让**原生控件**跟着浅色主题走 —— 滑块没填满的那截轨道原本是
     浏览器深色模式的默认黑，在浅色面板上非常刺眼。
     （P12 滑块默认停在 100%，看不出来；P13 停在真实仓位上就露出来了。） */
  color-scheme: light;
  /* P23.0：面板**铺满一屏**。
     宿主的 .neko-page 没有 min-height（body 上才有），所以危机态的
     「全屏边缘红光」用 box-shadow: inset 时**必须**自己把根节点撑到
     100vh，否则那道光只贴在内容顶上、根本铺不到视口边缘。 */
  min-height: 100vh;
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
.rrm-root .neko-card-header { padding: 10px 12px 0; }
.rrm-root .neko-card-body { padding: 10px 12px 12px; gap: 10px; }
.rrm-root .neko-card-title { color: #1f2d3d; font-size: 16px; font-weight: 700; letter-spacing: 0.5px; }
.rrm-root .neko-heading { color: #1f2d3d; }
.rrm-root .neko-text { color: #6b7a90; }

/* ── ③ 页面标题 + **P23.2 直播看板：锁死一屏** ─────────────────────
   注意：.rrm-root **就是** .neko-page 那个元素本身
   （宿主 Page 把两个 class 拼在**同一个节点**上）⇒ 这里必须写
   .rrm-root.rrm-live-page；
   写 .rrm-root .neko-page（后代选择器）**永远匹配不到自己** ——
   上一版那条 gap: 16px 其实一直是**死的**。── */
.rrm-root.rrm-live-page {
  /* 锁死一屏：**页面本身绝不出现纵向滚动条**。
     塞不下时由内部分区自己收（下面那条 min-height:0 链），
     而不是把整页撑出一条滚动条。 */
  height: 100vh;
  min-height: 0;
  overflow: hidden;
  padding: 8px 10px;
  gap: 8px;
  /* 局中页**不要** Page 的标题块（品牌名在标题行里已经有了）⇒
     Page 只有一个孩子 ⇒ 一行就够。 */
  grid-template-rows: minmax(0, 1fr);
  align-content: stretch;
}
/* 主列（看板本体）：min-height: 0 ⇒ 它才**肯**被压缩。 */
.rrm-root.rrm-live-page .rrm-live-stage { min-height: 0; }
/* 左右分栏：宿主 Split（grid），ratio="3fr 2fr" = 60/40。
   这条 flex 把它撑满看板区剩下的高度（Split 是主 Stack 的 flex item）。 */
.rrm-root.rrm-live-page .rrm-live-board {
  flex: 1 1 0;
  min-height: 0;
}
/* 两列：**必须** min-height:0 —— grid / flex 子项默认 min-height:auto，
   不写这行它们不肯被压缩（内容溢出 = 被裁，比滚动更糟）。 */
.rrm-root.rrm-live-page .rrm-live-col { min-height: 0; }
/* 工具条（状态徽标 + Yui 三档 + 节奏控制）：**固定高度**，不许被压缩。 */
.rrm-root.rrm-live-page .rrm-live-toolbar { flex: 0 0 auto; }
/* 「吃满剩余高度」的卡片（行情卡 / 交易台卡）：
   卡片自己改成 flex 列 ⇒ 里面的滚动区才有高度可分。 */
.rrm-root.rrm-live-page .rrm-live-flex {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.rrm-root.rrm-live-page .rrm-live-flex .neko-card-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* 宿主 ScrollArea：默认 max-height: 360px 会把它卡死 ⇒ 放开。
   （**只有这里能滚**。） */
.rrm-root.rrm-live-page .rrm-live-flex .neko-scroll-area {
  min-height: 0;
  max-height: none;
}
/* 行情列表：**吃满**行情卡剩下的高度。
   基准必须是 0 —— 用 auto 的话，它会先按内容高（20 只方块 ≈ 484px）
   参与列的分配，把 K 线卡挤到被裁（裁切比滚动更糟）。 */
.rrm-root.rrm-live-page .rrm-anchor-market-grid .neko-scroll-area {
  flex: 1 1 0;
}
/* 精调区：按**内容高度**（卡片本身也是按内容高度的）。 */
.rrm-root.rrm-live-page .rrm-trade-finetune-card .neko-scroll-area {
  flex: 0 1 auto;
}
/* 精调卡（买卖比例）：**按内容高度**（折叠时只有一行摘要，
   不该被拉成一个空盒子）；展开时自己长高，多余的空间让给下面。 */
.rrm-root.rrm-live-page .rrm-trade-finetune-card { flex: 0 1 auto; }
/* 每日动作卡 + 操作卡 **贴住右列底部** —— 主播的手够按钮，
   而按钮上方就是「钱」。 */
.rrm-root.rrm-live-page .rrm-daily-actions { margin-top: auto; }
/* ── ③ 页面标题（空闲页 / 结算页仍在用）─────────────────────────── */
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
/* P23.0：红涨绿跌（原为绿涨红跌）。浅底 + 深字，保证对比度。 */
.rrm-root .rrm-change-up { background: #fde9ed; color: #d6486a; }
.rrm-root .rrm-change-down { background: #e7f8f0; color: #17916f; }
.rrm-root .rrm-change-flat { background: #eef3fa; color: #7b8ca6; }

/* ── ⑦ K 线：柱子带纵向渐变（设计图的「光影」），底下垫一层网格 ─────── */
.rrm-root .rrm-kline-wrap {
  /* P27.1：K 线画布换成**深色圆角底** —— 珊瑚粉的涨柱和薄荷绿的跌柱
     在深底上才跳得出来（浅底上那两根柱子看着像褪了色）。
     网格线也跟着换成冷色，底与线都在同一套色温里。 */
  background-color: #131d33;
  background-image: repeating-linear-gradient(180deg, rgba(142, 178, 236, 0.12) 0px, rgba(142, 178, 236, 0.12) 1px, rgba(142, 178, 236, 0) 1px, rgba(142, 178, 236, 0) 40px);
  /* P23.0：巨幅 K 线要有一个**边界**，否则满屏渐变看着像一张散掉的画。 */
  border-radius: 16px;
  box-shadow: inset 0 0 0 1px rgba(120, 156, 214, 0.28), inset 0 -30px 60px rgba(6, 12, 26, 0.55);
}
/* P23.0：红涨绿跌（原为绿涨红跌）—— 柱子的渐变也跟着翻面：
   涨 = 珊瑚粉渐变，跌 = 薄荷绿渐变。渐变仍然只准纵向（180deg）。
   柱子的**弹光**是**内联** box-shadow（见 Kline 组件）——
   它要跟着每一根柱子的涨跌单独变色，CSS 变量做不到。 */
.rrm-root .rrm-kline-bar-up { background: linear-gradient(180deg, #ff8fa4 0%, rgba(255, 107, 129, 0.22) 100%); }
.rrm-root .rrm-kline-bar-down { background: linear-gradient(180deg, #6fd8bd 0%, rgba(90, 200, 168, 0.22) 100%); }

/* P21.0：新手兜底的「幽灵提示」—— 从下面浮上来一点点。
   只用 transform + transition；那几样会让宿主布局崩掉的东西一个没用。 */
.rrm-root .rrm-ghost-hint {
  transform: translateY(5px);
  transition: transform 240ms ease-out;
}
.rrm-root .rrm-ghost-hint.rrm-ghost-hint-in { transform: translateY(0); }

/* ── ⑧ 操作台的两个胶囊按钮：渐变质感 + 粗体 ───────────────────── */
/* P21.0：全屏时按钮跟着面板一起变大（vh = iframe 视口高度，
   vw = iframe 宽度）。
   clamp() 给上下限，窄窗口不至于挤爆。
   注意：这个注释在 CSS 模板字符串里，**一个反引号都不许出现**
   （P11.1 与 P12.0 各踩过一次：当场截断模板 ⇒ 面板白屏）。

   P23.0：按钮**巨幅化**（56px → 76px 起跳，11vh 跟着窗口长）——
   折叠起来的操作区里，屏幕上最该有的就是这两个按钮。

   P23.2：**改回紧凑档**（76px→56px 起跳，11vh→7vh）。
   原因：直播看板的右栏只有 40% 宽，巨号按钮会把**滑块、预览数字**
   一起挤没 —— 而那三样（滑块 / 预览 / 按钮）必须**同屏可见**，
   交易台才用得上。宁可按钮小一号，也不能让人为了调仓位去滚动。 */
.rrm-root .neko-button.rrm-pill {
  flex: 1 1 0;
  min-width: 0;
  height: clamp(56px, 7vh, 80px);
  padding: 0 16px;
  font-size: clamp(18px, 1.7vw, 28px);
  font-weight: 800;
  letter-spacing: 2px;
  border-radius: clamp(26px, 3.4vh, 38px);
  border: none;
  /* 真机教训：按钮被压窄时中文会**竖着排**（一字一行），难看且读不出来。
     锁死不换行，宁可两行空间也要横着排。 */
  white-space: nowrap;
  /* 按下去的时候「陷」一下 —— 只切 transform / box-shadow，
     不用 CSS 动画（0.8.0 的崩溃根因之一）。 */
  transition: transform 120ms ease-out, box-shadow 120ms ease-out;
}
/* P23.0：「掏钱买」= **赌徒红**（与全局「红涨绿跌」同源）——
   买红卖绿是中文炒股界面最扎手的一对颜色，正好也是赌徒感的来源。 */
.rrm-root .neko-button.rrm-pill-buy {
  background: linear-gradient(90deg, #ff9db0 0%, #e8455f 100%);
  color: #ffffff;
  box-shadow: 0 16px 38px rgba(232, 69, 95, 0.45), inset 0 2px 0 rgba(255, 255, 255, 0.35);
  text-shadow: 0 2px 6px rgba(122, 20, 40, 0.35);
}
/* P23.0：「割肉卖」= **薄荷绿**（跌色），跟买按钮拉开一个色系。 */
.rrm-root .neko-button.rrm-pill-sell {
  background: linear-gradient(90deg, #7fe3c8 0%, #23a887 100%);
  color: #04352a;
  box-shadow: 0 16px 38px rgba(35, 168, 135, 0.42), inset 0 2px 0 rgba(255, 255, 255, 0.40);
  text-shadow: 0 1px 3px rgba(4, 53, 42, 0.28);
}
/* P27.0：「做空」= **粉色**。
   它是第四种玩法（不是买也不是卖），所以单独占一个色系，
   不去挤 P23.0 那套「红涨绿跌」的语义。 */
.rrm-root .neko-button.rrm-pill-short {
  background: linear-gradient(90deg, #ffc7e3 0%, #ef5fa6 100%);
  color: #3d0b28;
  box-shadow: 0 16px 38px rgba(239, 95, 166, 0.42), inset 0 2px 0 rgba(255, 255, 255, 0.40);
  text-shadow: 0 1px 3px rgba(61, 11, 40, 0.25);
}
/* 按钮下面那行小字（设计图里的 All In / Stop Loss 副标题）
   P27.1：三键并排 ⇒ 小字也改成**三等分**（它们挪进【更多】弹窗了）。 */
.rrm-root .rrm-pill-captions { display: flex; gap: 8px; width: 100%; }
.rrm-root .rrm-pill-caption {
  flex: 1 1 0;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
/* P23.0：小字也跟着翻面（买红卖绿）。 */
.rrm-root .rrm-pill-caption-buy { color: #c9455f; }
.rrm-root .rrm-pill-caption-sell { color: #1d8f74; }
/* P27.1：做空那一条的小字跟粉按钮同色系。 */
.rrm-root .rrm-pill-caption-short { color: #b8437f; }

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
   整排**居中** —— 0.9.0 的 1fr 两列看着太散，像两张不相干的卡片。
   P27.1：底部操作台改成**三键并排**（掏钱买 / 割肉卖 / 做空）⇒ 等宽三列。 */
.rrm-root .neko-grid.rrm-trade-row {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  justify-content: center;
}
/* P28.0：街机档（arcade_simple）—— 三键变**两键**。
   键位行本身一个字都不动（三列那条规则是既有红线），
   街机档靠**根节点上的 rrm-arcade-simple** 把这一行改成两列：
   掏钱买 / 割肉卖 各占一半宽，仍然是**一整行**，绝不折行。 */
.rrm-root.rrm-arcade-simple .neko-grid.rrm-trade-row {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

/* ── P27.1：顶部概览（总资产 + 赚亏 + 现金红线）────────────────────
   三样都是「一眼要看」的东西 ⇒ 顶格放在工具条下面。
   里面的 BigStat 把自带的白底去掉，免得卡片里再套一层白盒子。 */
.rrm-root.rrm-live-page .rrm-live-overview { flex: 0 0 auto; min-height: 0; }
.rrm-root.rrm-live-page .rrm-live-overview .neko-card-body { gap: 6px; padding: 10px 14px 12px; }
.rrm-root.rrm-live-page .rrm-live-overview .neko-stat {
  background: transparent;
  border: none;
  box-shadow: none;
}
/* ── P27.1：底部操作台（整条压在下沿，三键并排）────────────────────
   注意：这张卡必须**原样**挂着教程锚点 rrm-anchor-trade-console
   （class 后面不许再追加别的 —— 教程按这个 class 圈焦点），
   所以这里用锚点本身当选择器，不另加 class。 */
.rrm-root.rrm-live-page .rrm-anchor-trade-console { flex: 0 0 auto; min-height: 0; }
.rrm-root.rrm-live-page .rrm-anchor-trade-console .neko-card-body {
  gap: 8px;
  padding: 10px 14px 12px;
}
/* 杠杆 / 保证金输入卡：紧贴三键上方，一行排开。 */
.rrm-root .rrm-short-panel {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 10px;
  border-radius: 12px;
  background: rgba(91, 143, 249, 0.08);
  border: 1px solid rgba(91, 143, 249, 0.22);
}
.rrm-root .rrm-short-panel .neko-select { max-width: 220px; }
.rrm-root .rrm-short-label { font-size: 12px; color: var(--muted); white-space: nowrap; }
.rrm-root .rrm-short-count { font-size: 12px; font-weight: 700; color: #d6486a; }
/* ── P27.1：资产曲线（K 线正下方，深色画布 + 图例）────────────────── */
.rrm-root .rrm-equity-inline { display: flex; flex-direction: column; gap: 6px; }
.rrm-root .rrm-equity-canvas {
  border-radius: 12px;
  overflow: hidden;
  background-color: #131d33;
  box-shadow: inset 0 0 0 1px rgba(120, 156, 214, 0.22);
}
.rrm-root .rrm-equity-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
}
.rrm-root .rrm-equity-taunt { color: #d6486a; font-weight: 700; }

/* ── P13：仓位控制（连续滑块 + 直输框）+ 实时预览 ──────────────────
   滑块是宿主**原生**的 input[type=range]（.neko-slider）—— 我们只调宽度，
   **不加任何定位、层叠、滤镜、透明**（0.8.0 的崩溃红线一条没破）。
   P13 把步进从 5% 改成 **1%**，并且**删掉了 25/50/75/100 那四档快捷键** ——
   仓位不再被量化成四档，玩家可以精确拖到 33% 或 67%。
   直输框也是宿主**原生**的 input（.neko-input），我们只调宽度与字号。 */
.rrm-root .neko-slider { width: 100%; }
/* P13：滑块轨道上色。
   宿主 UI Kit 用 accent-color 给**已填充**那段上色，但**没填充**那段
   走的是浏览器默认 —— 在这台机器的 Edge 上画出来是**纯黑**，放在浅色
   面板上非常刺眼（P12 滑块默认停在 100%，看不出来；P13 停在真实仓位上
   就露出来了）。
   ⇒ 这里用 CSS 变量 --rrm-trade-fill（由面板按当前比例写入）
   画一条 90 度渐变：左边是主色，右边是淡蓝。**没有**引入任何危险属性。 */
.rrm-root .rrm-trade-slider .neko-slider-input {
  /* **必须**先把原生外观去掉，作者写的 track / thumb 伪元素才会生效
     （否则 Chrome 用自己的内部绘制，作者的轨道色根本不认）。 */
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  height: 16px;
}
.rrm-root .rrm-trade-slider .neko-slider-input::-webkit-slider-runnable-track {
  height: 6px;
  border-radius: 3px;
  background: linear-gradient(
    90deg,
    var(--primary) 0%,
    var(--primary) var(--rrm-trade-fill, 0%),
    rgba(91, 143, 249, 0.2) var(--rrm-trade-fill, 0%),
    rgba(91, 143, 249, 0.2) 100%
  );
}
.rrm-root .rrm-trade-slider .neko-slider-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px;
  height: 16px;
  margin-top: -5px;
  border-radius: 50%;
  background: var(--primary);
  border: 2px solid #ffffff;
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
/* 直输框：两个框（股数 / 金额）平分一行，单位小字跟在框后。 */
.rrm-root .rrm-trade-inputs {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.rrm-root .neko-input.rrm-trade-input {
  flex: 1 1 0;
  min-width: 0;
  height: 28px;
  padding: 0 6px;
  font-size: 12px;
  border-radius: 8px;
}
.rrm-root .rrm-trade-input-unit {
  flex: 0 0 auto;
  font-size: 11px;
  color: #7b8ca6;
  white-space: nowrap;
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

/* P8.1：K 线数据不足时的提示语 —— 浅灰、居中、不抢戏。
   P27.1：画布换成深色底 ⇒ 这两行字也得提亮，不然糊在深底里看不见。 */
.rrm-root .rrm-kline-warming {
  color: #cbd9ee;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
/* P23.1：K 线的**占位背景** —— 底纹、描边跟真 K 线完全一样，
   所以「还没攒够点数」时那块地方**一点也不塌**（54vh 一直在那儿）。 */
.rrm-root .rrm-kline-placeholder {
  color: #9fb2c6;
}
.rrm-root .rrm-kline-placeholder-hint {
  margin-top: 8px;
  color: #9aa7bd;
  font-size: 12px;
  letter-spacing: 0.3px;
}
/* P23.1：插件离线时的「停摆」外观 —— 整块面板换成一块说明，
   没有满屏会失败的按钮（真机事故：截图 3 里的一串红色警告）。 */
.rrm-root.rrm-offline {
  background: #eef2f7;
}
.rrm-root .rrm-offline-block {
  width: 100%;
}
.rrm-root .neko-button.rrm-offline-retry {
  min-height: 44px;
  padding: 0 22px;
  border-radius: 22px;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #ffffff;
  background: linear-gradient(90deg, #8fb4ef 0%, #5b8ff9 100%);
  border: 1px solid rgba(91, 143, 249, 0.45);
  box-shadow: 0 8px 20px rgba(91, 143, 249, 0.28);
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

/* ── ⑬ P7.2 传下来的三档字体（只改字体，不改布局）──────────────────── */
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

/* ── P18.0：动态聚焦引导（**零危险 CSS**）────────────────────────────
   圈 = outline + box-shadow（两个都不在禁词表里）；
   遮罩用**宿主自带的** Modal（宿主自己的全屏遮罩，不是我们写的）。
   这一段里**不出现**定位 / 层叠 / 滤镜 / 裁剪 / 淡入淡出 / 关键帧。── */
.rrm-tutorial-target {
  outline: 2px solid #7bbcff;
  outline-offset: 3px;
  border-radius: 14px;
  box-shadow: 0 0 0 5px rgba(123, 188, 255, 0.24);
}
.rrm-tutorial-coach {
  border-color: rgba(123, 188, 255, 0.55);
  box-shadow: 0 10px 28px rgba(123, 188, 255, 0.16);
}
.rrm-tutorial-steps .neko-step-index {
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  color: #16305c;
}

/* ── P23.0：濒危视觉发病（危机态）──────────────────────────────────────
   三件事，**全部零危险 CSS**（没有定位、没有滤镜、没有淡入淡出、没有关键帧）：

   ① **全屏边缘红光** —— box-shadow: inset（不需要定位）。
      根节点自己补了 min-height: 100vh，所以这道光真的铺到视口边缘。
      **光敏性合规**：单次亮 ≤ 200ms、α ≤ 0.35、周期 ≥ 1.2s（见面板里的
      CRISIS_GLOW_* 常量）—— 不许把它改成高频闪烁。 */
.rrm-root.rrm-crisis-glow {
  box-shadow: inset 0 0 160px rgba(214, 72, 106, 0.30);
}
/* 危机态的**资金数字抖动**：只切 transform（JS 定时器驱动）。 */
.rrm-root .rrm-crisis-shake {
  will-change: transform;
}

/* ── P23.0：Yui 介入横幅（**专属于 Yui**，区别于玩家自己的操作）────────
   紫色调 + 爪印 —— 面板上其它地方没有这个颜色，一眼就知道"这是猫娘干的"。 */
.rrm-root .rrm-yui-banner {
  border-radius: 18px;
  border: 1px solid rgba(171, 104, 214, 0.38);
  background: linear-gradient(90deg, #ffe9f7 0%, #efe1ff 100%);
  box-shadow: 0 12px 34px rgba(171, 104, 214, 0.30), inset 0 1px 0 rgba(255, 255, 255, 0.55);
}
.rrm-root .rrm-yui-banner-title {
  color: #7b3fa0;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 1px;
}
.rrm-root .rrm-yui-banner-body { color: #5b4a72; font-size: 13px; line-height: 1.6; }
.rrm-root .rrm-yui-banner-paw { font-size: 22px; line-height: 1; }
/* 「呼吸」高光（JS 定时器切这两个 class，**不用 CSS 动画**）。 */
.rrm-root .rrm-yui-banner-hot {
  box-shadow: 0 16px 42px rgba(171, 104, 214, 0.52), inset 0 1px 0 rgba(255, 255, 255, 0.60);
}

/* ── P23.0：【耻辱柱】──────────────────────────────────────────────────
   历史破产记录表（名字 / 存活天数 / 破产原因），**按存活天数升序** ——
   越短越丢人，排在最前面。 */
.rrm-root .rrm-shame-btn {
  width: 100%;
  min-height: 52px;
  border-radius: 26px;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 1px;
  background: linear-gradient(90deg, #e7ecf7 0%, #dbe4f4 100%);
  color: #40506b;
  border: 1px solid rgba(142, 171, 219, 0.34);
  box-shadow: 0 8px 22px rgba(133, 163, 214, 0.20);
}
.rrm-root .rrm-shame-day { font-weight: 800; color: #c9455f; }
.rrm-root .rrm-shame-name { font-weight: 700; color: #2f3f58; }
.rrm-root .rrm-shame-reason { color: #6b7a90; }

/* ── P23.0：Yui 的**操控模式**选择（空闲态）──────────────────────────
   直播托管只有主播自己能开 —— Yui 自己不许升权。 */
.rrm-root .rrm-yui-mode-block {
  width: 100%;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(171, 104, 214, 0.22);
}
.rrm-root .neko-button.rrm-yui-mode-btn {
  min-height: 34px;
  padding: 0 14px;
  border-radius: 17px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #6b5a86;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(171, 104, 214, 0.26);
}
.rrm-root .neko-button.rrm-yui-mode-btn.rrm-yui-mode-btn-on {
  color: #ffffff;
  background: linear-gradient(90deg, #b98ae0 0%, #8b5fd0 100%);
  border: 1px solid rgba(139, 95, 208, 0.55);
  box-shadow: 0 8px 20px rgba(139, 95, 208, 0.30);
}

/* ── P23.2：局中「工具条」的小徽标（节奏 / Yui 模式）─────────────────
   空闲态那套大卡片继续留着；局中只留**能点的小药丸**，
   一行摆完，把整行的高度让给核心数据。── */
.rrm-root .neko-button.rrm-speed-tag.rrm-speed-tag-mini {
  width: auto;
  min-height: 26px;
  padding: 0 10px;
  border-radius: 13px;
  font-size: 12px;
  letter-spacing: 0;
}
.rrm-root .neko-button.rrm-pace-toggle.rrm-pace-toggle-mini {
  min-height: 26px;
  padding: 0 10px;
  border-radius: 13px;
  font-size: 12px;
  letter-spacing: 0;
}
.rrm-root .neko-button.rrm-pace-advance.rrm-pace-advance-mini {
  flex: 0 0 auto;
  min-height: 26px;
  padding: 0 12px;
  border-radius: 13px;
  font-size: 12px;
  letter-spacing: 0;
  box-shadow: 0 6px 16px rgba(123, 188, 255, 0.32);
}
.rrm-root .neko-button.rrm-yui-mode-btn.rrm-yui-mode-btn-mini {
  min-height: 26px;
  padding: 0 10px;
  border-radius: 13px;
  font-size: 12px;
  letter-spacing: 0;
}

/* P28.0：两个**独立**按钮（代打 / 双打）—— 按钮下面各挂一行说明。
   空闲态有空间讲清楚，局中（compact）只留小徽标。 */
.rrm-root .rrm-yui-duo-item { min-width: 0; }
.rrm-root .rrm-yui-duo-item .neko-text { font-size: 12px; line-height: 1.6; }

/* ── P24.0：Yui 的【话痨级别】药丸（安静 / 正常 / 话痨）─────────────────
   直播时没动静太干了 —— 这个开关**直接**管后台每隔几个 tick 开一次她的回合。
   配色刻意跟上面的「操控模式」（紫）分开：一个是**权限**，一个是**音量**。 */
.rrm-root .neko-button.rrm-yui-talk-btn {
  min-height: 34px;
  padding: 0 14px;
  border-radius: 17px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #3f6f86;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(107, 168, 200, 0.30);
}
.rrm-root .neko-button.rrm-yui-talk-btn.rrm-yui-talk-btn-on {
  color: #ffffff;
  background: linear-gradient(90deg, #6fb7e0 0%, #3f8fc4 100%);
  border: 1px solid rgba(63, 143, 196, 0.55);
  box-shadow: 0 8px 20px rgba(63, 143, 196, 0.30);
}
.rrm-root .neko-button.rrm-yui-talk-btn.rrm-yui-talk-btn-mini {
  min-height: 26px;
  padding: 0 10px;
  border-radius: 13px;
  font-size: 12px;
  letter-spacing: 0;
}
.rrm-root .rrm-yui-talk-inline {
  flex: 0 0 auto;
}
.rrm-root .rrm-yui-talk-block {
  width: 100%;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(107, 168, 200, 0.22);
}

/* ── P23.0：操作台的「精调」折叠区 ─────────────────────────────────────
   极简模式下折叠起来（只留两个巨号按钮 + 一行仓位摘要），
   想改仓位再点开。 */
.rrm-root .neko-button.rrm-trade-finetune {
  width: 100%;
  min-height: 40px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #5b6b82;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(142, 171, 219, 0.30);
}
.rrm-root .rrm-trade-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 10px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 1.5;
  color: #5b6b82;
  background: rgba(91, 143, 249, 0.08);
  border: 1px solid rgba(91, 143, 249, 0.22);
}
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

/**
 * P23.0：濒危发病的三条**节奏常量**。
 *
 * 「全屏边缘红光」是**红闪** —— 必须守住光敏性癫痫的合规上限：
 *
 * * 单次亮起 **≤ 200ms**（``CRISIS_GLOW_ON_MS``）；
 * * 周期 **≥ 1.2s**（``CRISIS_GLOW_CYCLE_MS``）；
 * * 亮度 **α ≤ 0.35**（写在 CSS 的 ``box-shadow`` 里）。
 *
 * 任何一条被改松，直播现场的观众就有可能出事 —— 这不是审美问题。
 */
const CRISIS_GLOW_ON_MS = 180
const CRISIS_GLOW_CYCLE_MS = 1400

/**
 * P23.0：危机态资金数字的**抖动频率**（毫秒）。
 *
 * 90ms ≈ 11 帧/秒 —— 看着像"手在抖"，但不至于让人晕。
 */
const CRISIS_SHAKE_INTERVAL_MS = 90

/**
 * P21.0：**三档 UI 密度**（渐进式简化 —— 把复杂藏起来，别在新手村吓人）。
 *
 * P16.0 只有「简单 / 完整」两档，前两关一起走简单面板。
 * P21.0 拆成三档，因为**入门档和街机档的诉求不一样**：
 *
 * | 档位 | 密度 | 诉求 |
 * | :-- | :-- | :-- |
 * | 街机 | ``minimal`` | 纯爽局：只留核心玩法区块，别吓跑人 |
 * | 入门 | ``guarded`` | 开始教生存：恢复水位条，但还不玩多标的计划 |
 * | 进阶 / 地狱 / 无尽 | ``full`` | 火力全开：压迫感该在那里出现 |
 *
 * **只影响面板的显示**（条件渲染），**不影响任何底层计算** ——
 * 引擎层与模式层一行没动，500 局基线照样逐字节全等。
 */
type UiLevel = "minimal" | "guarded" | "full"

/** 街机（纯爽局）：核心玩法之外什么都不给看。 */
const MINIMAL_UI_MODES = ["classic_arcade"]
/** 入门（断供前夕）：恢复生存提示，但仍不玩多标的计划。 */
const GUARDED_UI_MODES = ["classic_beginner"]
/** 前两关的持仓台账只铺前 N 只（其余折成一行字）。 */
const SIMPLE_POSITION_LIMIT = 3

/** 档位 ⇒ UI 密度（认不出来的档位一律按 ``full`` —— 宁可多给，不给空）。 */
function uiLevelFor(mode: string): UiLevel {
  if (MINIMAL_UI_MODES.indexOf(mode) >= 0) return "minimal"
  if (GUARDED_UI_MODES.indexOf(mode) >= 0) return "guarded"
  return "full"
}

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
 * 涨跌数字：涨**红**跌**绿**（P23.0 翻面；P9.0 原为涨绿跌红）。
 *
 * 用的是设计图那一档**柔和**的珊瑚粉 / 薄荷绿 —— 它们要落在浅色胶囊底上，
 * 不能用纯饱和色（浅底 + 亮字会糊成一片）。
 *
 * ⚠ 只用于**价格方向**（涨跌幅、持仓盈亏）。危险语义走 ``--danger``，
 * 两边不要混。
 */
function changeColor(value: number | undefined | null): string {
  const pct = Number(value || 0) * 100
  if (pct > 0.001) return "#d6486a"
  if (pct < -0.001) return "#17916f"
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
  /**
   * 字号：数字（px）或 **CSS 长度表达式**（P21.0）。
   *
   * P21.0：全屏时希望核心数字跟着面板一起变大 ⇒ 传
   * ``clamp(30px, 3.4vw, 56px)`` 这样的值（``vw`` = iframe 宽度）。
   */
  size?: number | string
  /**
   * P16.0：跳动时的**强调**（缩一下）。
   *
   * 只切 `transform` —— **不用 CSS 动画**（0.8.0 的崩溃根因之一）。
   * 不跳的时候给 `undefined`（不留 `scale(1)`）—— 免得凭空建一个
   * containing block。
   */
  pop?: boolean
  /**
   * P23.0：老虎机的**闪光**（赚绿 / 亏红）。
   *
   * 只加 `text-shadow` —— 不用 `filter`（禁词），也不用 CSS 动画。
   */
  glow?: "" | "up" | "down"
  /**
   * P23.0：濒危发病的**随机抖动**（像素偏移）。
   *
   * 现金跌破饭钱红线时，资金数字会被 JS 定时器随机推一下。
   */
  shake?: { x: number; y: number }
}) {
  const transforms: string[] = []
  if (props.pop) transforms.push("scale(1.12)")
  const shake = props.shake
  if (shake && (shake.x !== 0 || shake.y !== 0)) {
    transforms.push(`translate(${shake.x}px, ${shake.y}px)`)
  }
  return h(
    "div",
    { className: "neko-stat" },
    h("span", { className: "neko-stat-label" }, props.label),
    h(
      "strong",
      {
        className: shake ? "neko-stat-value rrm-crisis-shake" : "neko-stat-value",
        style: {
          fontSize:
            typeof props.size === "string" ? props.size : `${props.size || 34}px`,
          color: props.color,
          lineHeight: "1.15",
          transform: transforms.length > 0 ? transforms.join(" ") : undefined,
          // P23.0：赚闪绿、亏闪红 —— 两套语义色（--success / --danger），
          // **不是**涨跌色（--up / --down）：这里的红绿说的是"赚没赚"。
          textShadow:
            props.glow === "up"
              ? "0 0 18px rgba(23, 145, 111, 0.85), 0 0 42px rgba(23, 145, 111, 0.38)"
              : props.glow === "down"
                ? "0 0 18px rgba(214, 72, 106, 0.85), 0 0 42px rgba(214, 72, 106, 0.38)"
                : undefined,
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
 * P16.0：**连击徽章** —— 街机档的「爽感」配件。
 *
 * 连续买中上涨的股票时，这块徽章会亮起来并**弹一下**。
 *
 * **不用 CSS 动画**（0.8.0 的崩溃根因之一）：弹跳是 **JS 定时器**
 * 切 `transform` 做的，跟 P7.2 的濒临破产闪烁同款思路。
 *
 * 徽章**只展示**连击数 —— 不含任何游戏数值，也不参与任何判定。
 */
function ComboBadge(props: { streak: number; best: number; t: Translate }) {
  const streak = Math.max(0, Number(props.streak || 0))
  const best = Math.max(0, Number(props.best || 0))
  const t = props.t
  // 连击断了 ⇒ 徽章收起来（只留一行「最长连击」）。
  const on = streak >= 2
  const [pop, setPop] = useState(false)
  useEffect(() => {
    if (!on) {
      setPop(false)
      return
    }
    setPop(true)
    const timer = setTimeout(() => setPop(false), 260)
    return () => clearTimeout(timer)
  }, [streak, on])
  if (!on) {
    return best >= 2
      ? h(
          "div",
          { style: { fontSize: "12px", color: "var(--muted)" } },
          t("panel.combo_best", { count: best })
        )
      : null
  }
  return h(
    "div",
    {
      className: "rrm-combo",
      style: {
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "6px 12px",
        borderRadius: "12px",
        background: "rgba(91, 143, 249, 0.12)",
        border: "1px solid rgba(91, 143, 249, 0.35)",
        transform: pop ? "scale(1.06)" : undefined,
      },
    },
    h("span", { style: { fontSize: "18px" } }, "🔥"),
    h(
      "strong",
      { style: { fontSize: "20px", color: "var(--primary)" } },
      t("panel.combo_badge", { count: streak })
    ),
    h(
      "span",
      { style: { fontSize: "12px", color: "var(--muted)" } },
      t("panel.combo_hint")
    )
  )
}

/**
 * P14.0：**资金水位条** —— 把「明天的生活费」画成一条看得见的红线。
 *
 * **为什么要有它**：现金以前只是一个数字，玩家看不见「还剩多少能撑」。
 * 水位条把两件事摆到同一根尺子上：
 *
 * * **红区**（长度固定 = 明天要交的生活费）—— 掉进去就是断供；
 * * **青蓝区**（生活费之上的那部分）—— 还能花的余量。
 *
 * **危机态**（现金 < 明天的生活费）：整条变红并**轻微闪烁**，
 * 现金数字也一起变红闪烁 —— 极强的警示。
 *
 * 闪烁是 **JS 定时器切颜色**做的（跟 P7.2 的濒临破产数字同款），
 * **不用 CSS 动画**（0.8.0 的崩溃根因之一）。
 *
 * 样式全部走**内联 style**：P14 的纪律是「极度克制地使用 CSS」，
 * 所以这一版**一行新 CSS 都不加**。
 */
function CashGauge(props: {
  cash: number
  bill: number
  crisis: boolean
  alarmOn: boolean
  t: Translate
  /**
   * P16.0：**紧凑模式**（前两关用）。
   *
   * 前两关的诉求是「闭着眼睛瞎买也能赚钱」—— 复杂的水位条在那里是
   * **干扰**。紧凑模式把它压成**一行字**：红线多少、还差多少 / 还能花多少。
   */
  compact?: boolean
}) {
  const cash = Math.max(0, Number(props.cash || 0))
  const bill = Math.max(0, Number(props.bill || 0))
  const crisis = Boolean(props.crisis)
  // 尺子：至少要装得下「现金」和「生活费」两个数。
  const scale = Math.max(cash, bill, 1)
  // 红区长度固定 = 生活费（不随现金变）；青蓝区 = 超出生活费的那部分。
  const redW = crisis ? (cash / scale) * 100 : (Math.min(bill, scale) / scale) * 100
  // 危机态：红区里没填满的那一段 = 「还差多少」。
  const holeW = crisis ? Math.max(0, ((bill - cash) / scale) * 100) : 0
  const cyanW = crisis ? 0 : Math.max(0, ((cash - bill) / scale) * 100)

  // 闪烁 = 两个红色之间来回切（同一支红，只是深浅不同）。
  const redStrong = props.alarmOn ? "rgba(214, 72, 106, 0.42)" : "var(--danger)"
  const redSolid = "var(--danger)"
  const redZone = crisis ? redStrong : redSolid
  const valueColor = crisis
    ? props.alarmOn
      ? "rgba(214, 72, 106, 0.45)"
      : "var(--danger)"
    : "var(--text)"

  // ── P16.0：紧凑模式（前两关）—— 水位条**压成一行** ──────────────────
  // 复杂图形在前两关是**干扰**：玩家不需要研究它，只需要知道自己要交多少。
  if (props.compact) {
    return h(
      "div",
      {
        // P19.0：**紧凑版也要带锚点 class** —— 前两关（街机 / 入门）的
        // 教程第 4 步就是「看水位」，锚点叫 ``gauge``。
        // 之前紧凑分支漏了这个 class ⇒ 教程圈不到任何东西。
        className: "rrm-gauge rrm-anchor-gauge",
        style: {
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "8px",
          fontSize: "12px",
          color: "var(--muted)",
        },
      },
      h("span", null, props.t("panel.gauge_bill", { amount: money(bill) })),
      crisis
        ? h(
            "span",
            { style: { color: "var(--danger)", fontWeight: "700" } },
            props.t("panel.gauge_crisis", { gap: money(bill - cash) })
          )
        : h("span", null, props.t("panel.gauge_free", { amount: money(cash - bill) }))
    )
  }

  return h(
    "div",
    {
      className: "rrm-gauge rrm-anchor-gauge",
      style: { display: "flex", flexDirection: "column", gap: "6px" },
    },
    h(
      "div",
      {
        style: {
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "8px",
        },
      },
      h(
        "span",
        { style: { fontSize: "12px", color: "var(--muted)" } },
        props.t("panel.gauge_title")
      ),
      h(
        "span",
        { style: { fontSize: "13px", fontWeight: "700", color: valueColor } },
        props.t("panel.gauge_bill_amount", { amount: money(bill) })
      )
    ),
    h(
      "div",
      {
        className: "rrm-gauge-track",
        style: {
          display: "flex",
          width: "100%",
          height: "14px",
          borderRadius: "7px",
          overflow: "hidden",
          background: "rgba(142, 171, 219, 0.16)",
          border: crisis
            ? `1px solid ${props.alarmOn ? "rgba(214, 72, 106, 0.42)" : "var(--danger)"}`
            : "1px solid rgba(142, 171, 219, 0.18)",
        },
      },
      redW > 0
        ? h("div", {
            className: "rrm-gauge-red",
            style: { width: `${redW}%`, height: "100%", background: redZone },
          })
        : null,
      holeW > 0
        ? h("div", {
            className: "rrm-gauge-hole",
            style: {
              width: `${holeW}%`,
              height: "100%",
              background: "rgba(214, 72, 106, 0.26)",
            },
          })
        : null,
      cyanW > 0
        ? h("div", {
            className: "rrm-gauge-cyan",
            style: { width: `${cyanW}%`, height: "100%", background: "var(--primary)" },
          })
        : null
    ),
    h(
      "div",
      {
        style: {
          display: "flex",
          justifyContent: "space-between",
          gap: "8px",
          fontSize: "11px",
          color: "var(--muted)",
        },
      },
      h("span", null, props.t("panel.gauge_bill", { amount: money(bill) })),
      crisis
        ? h(
            "span",
            { style: { color: "var(--danger)", fontWeight: "700" } },
            props.t("panel.gauge_crisis", { gap: money(bill - cash) })
          )
        : h("span", null, props.t("panel.gauge_free", { amount: money(cash - bill) }))
    )
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
 * P27.0：**玩家 vs 内置猫娘 Yui** 的双线迷你折线图。
 *
 * 两条线**共用一把纵轴尺子**（同一个 min/max），不然两条线一比就没意义了。
 * 玩家是**蓝线**，Yui 是**红线** —— 这两个颜色只是"谁是谁"的标记，
 * 不参与 P23.0 那套"红涨绿跌"的语义。
 *
 * 画不了 canvas 就**降级成纯 div 柱子**（不依赖定位 / 动画），绝不崩面板。
 */
const EQUITY_PLAYER_COLOR = "#5B8FF9"
const EQUITY_YUI_COLOR = "#F2677F"

function EquityBattle(props: {
  player: number[]
  yui: number[]
  title?: string
  width?: number
  height?: number
}) {
  const canvasRef = useRef<any>(null)
  const [broken, setBroken] = useState(false)
  const playerValues = props.player || []
  const yuiValues = props.yui || []
  const signature = playerValues.join(",") + "|" + yuiValues.join(",")

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
      const width = Number(canvas.width) || 240
      const height = Number(canvas.height) || 72
      ctx.clearRect(0, 0, width, height)
      const series = [playerValues, yuiValues]
      const colors = [EQUITY_PLAYER_COLOR, EQUITY_YUI_COLOR]
      let max = 0
      let min = 0
      let seen = false
      for (const item of series) {
        for (const value of item) {
          if (!seen) {
            max = value
            min = value
            seen = true
            continue
          }
          if (value > max) max = value
          if (value < min) min = value
        }
      }
      if (!seen) return
      const span = max - min || 1
      const pad = 4
      const count = Math.max(playerValues.length, yuiValues.length)
      const step = count > 1 ? (width - pad * 2) / (count - 1) : 0
      for (let s = 0; s < series.length; s += 1) {
        const values = series[s]
        const color = colors[s]
        if (values.length === 0) continue
        ctx.strokeStyle = color
        ctx.lineWidth = 2
        ctx.lineJoin = "round"
        ctx.lineCap = "round"
        ctx.beginPath()
        for (let index = 0; index < values.length; index += 1) {
          const x = pad + step * index
          const y = height - pad - ((values[index] - min) / span) * (height - pad * 2)
          if (index === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.stroke()
        const tail = values[values.length - 1]
        const tailX = pad + step * (values.length - 1)
        const tailY = height - pad - ((tail - min) / span) * (height - pad * 2)
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(tailX, tailY, 2.5, 0, Math.PI * 2)
        ctx.fill()
      }
    } catch (error) {
      setBroken(true)
    }
  }, [signature])

  if (broken) {
    // 兜底：画不了 canvas 就用柱子（纯 div，无背景无定位）。
    const last = playerValues.slice(-16)
    const max = last.length ? Math.max.apply(null, last) : 0
    const min = last.length ? Math.min.apply(null, last) : 0
    const span = max - min || 1
    const bars = last.map((value, index) => {
      const previous = index > 0 ? last[index - 1] : value
      return h("div", {
        key: index,
        style: {
          width: "3px",
          height: `${Math.max(2, Math.round(((value - min) / span) * 28))}px`,
          marginRight: "1px",
          background: value >= previous ? EQUITY_PLAYER_COLOR : EQUITY_YUI_COLOR,
        },
      })
    })
    return h(
      "div",
      {
        title: props.title,
        className: "rrm-equity",
        style: {
          display: "flex",
          alignItems: "flex-end",
          height: "28px",
        },
      },
      ...bars
    )
  }

  return h("canvas", {
    ref: canvasRef,
    className: "rrm-equity",
    width: props.width || 240,
    height: props.height || 72,
    title: props.title,
    style: { display: "block", width: "100%" },
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
      // P14.0：这只占了**总仓位的百分之多少** —— 玩家得看得见钱压在哪儿。
      h(
        "span",
        {
          style: { fontSize: "12px", fontWeight: "600", color: "var(--muted)" },
          title: t("panel.weight"),
        },
        `${t("panel.weight")} ${percent(Number(position.weight || 0))}`
      ),
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

/**
 * P21.0：K 线高度随**宿主 iframe 的高度**自适应。
 *
 * ``vh`` 在 iframe 里指的是 **iframe 自己的视口高度** —— 也就是插件面板
 * 的高度。全屏时它变高 ⇒ K 线跟着长高，正好填满上半部分，
 * 不会在大屏下留一大片白底。
 *
 * ``clamp()`` 给上下限：窄窗口不至于挤成一条线，大窗口也不会长到失控。
 *
 * **只用长度单位**（不碰 position / filter / opacity）——
 * 0.8.0 崩 UI 的根因就是那几样东西。
 *
 * P23.0：**巨幅化**（34vh → 54vh，下限 232px → 320px）。
 * 这一版的核心诉求是「极简纯爽的大 K 线主视觉」——
 * K 线要**占掉局中面板的半个屏幕**，剩下的内容（交易台等）统统滚到下面去。
 *
 * P23.2：直播看板布局下**不再独占半屏** —— 它是左列（60%）的**主视觉**，
 * 高度压到 ``clamp(240px, 35vh, 450px)``：
 * 54vh 会把整页顶出滚动条，而直播时**一屏尽览**比「大」更要紧。
 * 剩下的高度让给行情方块、持仓方块和右列的钱包区。
 *
 * P27.1：面板改成「顶部概览 + 中部图表 + 底部三键操作台」的一屏结构，
 * K 线必须**再让**一截高度出来（下面要塞资产曲线，右侧还要塞持仓卡）：
 * 压到 ``clamp(210px, 26vh, 330px)`` —— 下限仍守在 200px 以上
 * （它依然是主视觉，不能塌成一根线）。
 */
const KLINE_HEIGHT_CSS = "clamp(210px, 26vh, 330px)"

function Kline(props: {
  points: KlinePoint[]
  emptyText: string
  warmingText: string
}) {
  const points = props.points || []
  // P8.0：K 线是**上半区的主视觉** —— 更高、更抢眼。
  // P21.0：高度改成 CSS 值（跟着 iframe 高度走），柱子的高度改用**百分比** ——
  // 百分比是相对容器算的，所以容器长高时柱子自动跟着长高（不需要 JS 测量）。
  const height = KLINE_HEIGHT_CSS
  // P23.1：**K 线区绝对不许塌陷**。
  //
  // 真机事故：引擎的 ``kline`` 是**空数组起步**（引擎 `market.py` 里
  // ``kline=[]``，第一次 harvest 才有第一个点），所以开局那一刻面板拿到
  // 0 个点 ⇒ 走 ``points.length === 0`` 分支 ⇒ 而那个分支**根本没给高度** ⇒
  // 54vh 的大 K 线塌成了一行字，玩家看到的就是「有下拉框、没图」。
  //
  // 现在两个"点数不够"的分支**合并**成一个占满高度的占位背景：
  // 底纹跟真 K 线一样（同一套网格 + 描边），所以那块地方**一直是那么大**，
  // 点数攒够了柱子直接顶上来，不会有任何跳版。
  if (points.length < MIN_KLINE_POINTS) {
    return h(
      "div",
      {
        className: "rrm-kline-wrap rrm-kline-placeholder",
        style: {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          height,
        },
      },
      h("span", { className: "rrm-kline-warming" }, props.warmingText),
      props.emptyText
        ? h("span", { className: "rrm-kline-placeholder-hint" }, props.emptyText)
        : null
    )
  }
  const prices = points.map((point) => point[1])
  const max = Math.max.apply(null, prices)
  const min = Math.min.apply(null, prices)
  const span = max - min || 1
  const bars = points.map((point, index) => {
    const tick = point[0]
    const price = point[1]
    // 柱子高度用**百分比**（相对容器），所以容器自适应高度时柱子跟着自适应。
    const ratio = (price - min) / span
    const previous = index > 0 ? points[index - 1][1] : price
    const rising = price >= previous
    // P9.0：柱子改成**纵向渐变**（顶上实、底下化开），底下再垫一层网格线 ——
    // 这就是设计图里那条 K 线区的「光影」。
    //
    // P23.0：**弹光**。每一根柱子都带着自己的光晕（内联 ``box-shadow``）——
    // 涨的珊瑚粉往外洇一层，跌的薄荷绿往外洇一层。
    // 为什么是**内联**而不是 CSS 类：光晕要跟着**这一根柱子的涨跌**走，
    // CSS 变量做不到（而且 ``filter`` 是禁词）。
    return h("div", {
      key: tick,
      title: `tick ${tick} · ${price.toFixed(4)}`,
      className: rising ? "rrm-kline-bar-up" : "rrm-kline-bar-down",
      style: {
        flex: "1 1 0",
        minWidth: "4px",
        maxWidth: "22px",
        height: `${(ratio * 100).toFixed(2)}%`,
        // 贴底的那根也要看得见（百分比算出来可能是 0）。
        minHeight: "3px",
        marginRight: "3px",
        borderRadius: "4px",
        boxShadow: rising
          ? "0 0 14px rgba(242, 103, 127, 0.85), 0 0 32px rgba(242, 103, 127, 0.32)"
          : "0 0 14px rgba(53, 191, 160, 0.85), 0 0 32px rgba(53, 191, 160, 0.32)",
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
        height,
        padding: "6px 0",
        overflowX: "auto",
      },
    },
    ...bars
  )
}

/**
 * P23.1：Yui 的**玩法**选择器（空闲态与局中共用同一份）。
 *
 * P23.0 把它只放在空闲态 ⇒ 真机死锁：
 * 「为了切换直播托管，必须先打开面板；打开面板就进了游戏；
 * 进了游戏就不能切换模式了。」—— 玩家被卡死在中间。
 *
 * 现在局中也有一份，主播在局中随时能改（切模式只是改一个设置项，
 * **不碰结算状态**，所以局中切换不会有任何冲突）。
 *
 * P28.0：从「三档选择器」改成**两个独立按钮**——
 * · 【Yui 全权代打（纯看戏）】= ``live``：你全程不用管，她自动买卖；
 * · 【人机协同双打】        = ``duo`` ：你自己手动买卖，她只点评不碰账户。
 *
 * 两个按钮走**同一个**动作（``yui_mode``），只是参数不同。
 */
function YuiModePicker(props: {
  mode: string
  t: Translate
  onPick: (mode: string) => void
  compact?: boolean
}) {
  // ── P28.0：两个按钮的文案**全部走新键**（旧的 yui_mode_* 留在词典里）──
  const items = [
    {
      id: "live",
      title: props.t("panel.yui_duo_live_title"),
      hint: props.t("panel.yui_duo_live_hint"),
      badge: props.t("panel.yui_duo_badge_live"),
    },
    {
      id: "duo",
      title: props.t("panel.yui_duo_team_title"),
      hint: props.t("panel.yui_duo_team_hint"),
      badge: props.t("panel.yui_duo_badge_team"),
    },
  ]
  // 后端还认 assist / spectate 两个旧值（语义都是「她不碰你的账户」）
  // ⇒ 面板上**归到「双打」**那一档，免得两个按钮一个都不亮。
  const duoLike =
    props.mode === "duo" || props.mode === "assist" || props.mode === "spectate"
  const buttonClass = (id: string, mini: boolean): string => {
    const on = id === "duo" ? duoLike : props.mode === id
    const base = on
      ? "rrm-yui-mode-btn rrm-yui-mode-btn-on"
      : "rrm-yui-mode-btn"
    return mini ? `${base} rrm-yui-mode-btn-mini` : base
  }
  // ── P23.2：局中（compact）只留**一排小徽标** ────────────────────
  //
  // 标题和说明在直播看板里都是噪音：两个按钮自己会说话
  // （选中的那个是高亮的），而每一像素都要留给核心数据。
  // 空闲态仍然用完整版（那边有空间讲清楚两个按钮差在哪）。
  if (props.compact) {
    return (
      <Inline gap={6} wrap={false} align="center" className="rrm-yui-mode-inline">
        {items.map((item) => (
          <Button
            key={item.id}
            className={buttonClass(item.id, true)}
            onClick={() => props.onPick(item.id)}
          >
            {item.badge}
          </Button>
        ))}
      </Inline>
    )
  }
  return (
    <Stack gap={8} className="rrm-yui-mode-block">
      <Text>{props.t("panel.yui_mode_title")}</Text>
      {items.map((item) => (
        <Stack key={item.id} gap={4} className="rrm-yui-duo-item">
          <Button
            className={buttonClass(item.id, false)}
            onClick={() => props.onPick(item.id)}
          >
            {item.title}
          </Button>
          <Text>{item.hint}</Text>
        </Stack>
      ))}
    </Stack>
  )
}

/**
 * P24.0：Yui 的【话痨级别】选择器（安静 / 正常 / 话痨）。
 *
 * 直播时没动静太干了 —— 这个开关直接决定后台**每隔几个 tick**
 * 开一次她的回合（安静档 = 只在出大事时开口）。
 *
 * 形态跟 ``YuiModePicker`` 的紧凑档一样：一排小药丸，
 * 坐在状态工具条里 —— 不占地方，但**随时能调**。
 */
function YuiTalkPicker(props: {
  level: string
  t: Translate
  compact?: boolean
  onPick: (level: string) => void
}) {
  const items = [
    { id: "quiet", label: props.t("panel.yui_talk_quiet") },
    { id: "normal", label: props.t("panel.yui_talk_normal") },
    { id: "chatty", label: props.t("panel.yui_talk_chatty") },
  ]
  const buttonClass = (id: string, mini: boolean): string => {
    const base =
      props.level === id
        ? "rrm-yui-talk-btn rrm-yui-talk-btn-on"
        : "rrm-yui-talk-btn"
    return mini ? `${base} rrm-yui-talk-btn-mini` : base
  }
  const pills = (
    <Inline gap={6} wrap={false} align="center" className="rrm-yui-talk-inline">
      {items.map((item) => (
        <Button
          key={item.id}
          className={buttonClass(item.id, Boolean(props.compact))}
          onClick={() => props.onPick(item.id)}
        >
          {item.label}
        </Button>
      ))}
    </Inline>
  )
  // ── 局中（紧凑档）：只留一排药丸，标题与说明都是噪音 ──────────
  if (props.compact) return pills
  // ── 空闲态：把三档分别意味着什么讲清楚（这是玩家第一次见它）──
  return (
    <Stack gap={6} className="rrm-yui-talk-block">
      <Text>{props.t("panel.yui_talk_title")}</Text>
      {pills}
      <Text>{props.t("panel.yui_talk_hint")}</Text>
    </Stack>
  )
}

/**
 * P23.1：宿主在插件**没运行**时抛出来的错误长这样：
 * ``Error { code: "PLUGIN_NOT_RUNNING", status: 409 }``
 * （见宿主 ``ui-kit/runtime.js`` 的 ``createHostedBridgeError``）。
 *
 * 真机事故：插件停了，面板还照常渲染按钮，玩家一按就弹出一堆红色警告。
 * 现在认准这个 code ⇒ 面板**整体停摆**（换成一块"插件已停止"的提示），
 * 而不是继续让人按一堆注定失败的按钮。
 *
 * 注意：**不许**在这里写中文（面板代码零硬编码中文）——
 * 只认 ASCII 的 code / status。
 */
function isPluginOfflineError(error: any): boolean {
  if (!error) return false
  if (String(error.code || "") === "PLUGIN_NOT_RUNNING") return true
  return Number(error.status || 0) === 409
}

/**
 * P26.0：**面板动作的硬超时**（毫秒）。
 *
 * 直播事故剧本：宿主的 ``props.api.call`` 在插件卡住时**永远不返回** ⇒
 * 面板的全局锁 ``busyRef`` 永远是 true ⇒ 玩家再按任何按钮都没反应，
 * 面板看起来就是"不动了"（观众会以为直播断线）。
 * 宿主自带的 ``timeoutMs`` 是 30 秒，对直播来说太久。
 *
 * 所以这里自己加一道 **3 秒**的闸：超时 ⇒ 立刻解锁 + 黄色 Toast，
 * 让玩家还能继续按下一个按钮（哪怕后端这一次真的没成功）。
 */
const ACTION_TIMEOUT_MS = 3000

/**
 * P26.0：给一个 promise 加**硬超时**。
 *
 * 注意：这不是"取消"（宿主那个 30 秒的 ``timeoutMs`` 才会真的 abort），
 * 这里只是**不再等它** —— 原 promise 继续在后台跑，回来以后被丢弃。
 */
function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      const error: any = new Error("timeout")
      error.__rrmTimeout = true
      reject(error)
    }, ms)
    promise.then(
      (value) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        reject(error)
      }
    )
  })
}

/** P26.0：认"我们自己那道闸"打出来的超时（和宿主自己的错误区分开）。 */
function isActionTimeoutError(error: any): boolean {
  return Boolean(error && error.__rrmTimeout === true)
}

/**
 * P26.0：面板**白屏防御**的兜底块。
 *
 * ErrorBoundary 抓到任何渲染异常 ⇒ 整个面板换成这一块
 * 「重连中，请稍后」+ 一个【重新渲染】按钮，
 * **绝不允许出现纯白屏**（白屏 = 观众以为直播挂了）。
 */
function renderPanelFallback(t: Translate, error: any, reset: () => void) {
  return h(
    Page,
    { title: t("panel.render_error_title") },
    h(
      Stack,
      { gap: 8 },
      h(Alert, { tone: "warning" }, t("panel.render_error_body")),
      h(Button, { onClick: reset }, t("panel.render_error_retry"))
    )
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
 * P18.0：**动态聚焦引导** —— 分档次教程的面板侧实现。
 *
 * 三条硬约束：
 *
 * 1. **零危险 CSS** —— 遮罩用**宿主自带的** ``Modal``（宿主自己的
 *    全屏遮罩，不是我们写的）；高亮圈只用 ``outline`` + ``box-shadow``；
 *    **不碰**定位 / 层叠 / 滤镜 / 裁剪 / 淡入淡出 / 关键帧。
 * 2. **台词不在面板里** —— 面板只显示**功能性 UI 文案**（步骤标题、正文、
 *    按钮名），全部走 i18n；猫娘说的话在**面板外**的外置气泡里。
 * 3. **上课时绝不挡路** —— 上课中的那一步用**教练条**（不遮挡），
 *    这样玩家**能点到被圈出来的真实元素**（真实操作才能推进教程）。
 *    只有开场与结课用全屏 ``Modal``（那两个时刻玩家只需要读 + 确认）。
 */
function TutorialOverlay(props: {
  tutorial: NonNullable<State["tutorial"]>
  t: Translate
  onBegin: () => void
  onAdvance: () => void
  onSkip: () => void
}) {
  const tutorial = props.tutorial || {}
  const phase = String(tutorial.phase || "idle")
  const t = props.t
  const index = Number(tutorial.index || 0)
  const total = Number(tutorial.total || 0)
  const step = tutorial.step || null
  const stepTitle = step && step.title_key ? t(step.title_key) : ""
  const stepBody = step && step.body_key ? t(step.body_key) : ""

  if (phase === "greet") {
    return h(
      Modal,
      {
        open: true,
        title: t("tutorial.greet.title"),
        onClose: props.onSkip,
        closeOnBackdrop: false,
        closeOnEscape: false,
        size: "md",
        children: h(
          Stack,
          { gap: 12 },
          h(Text, null, t("tutorial.greet.body")),
          h(
            "div",
            { className: "rrm-tutorial-steps" },
            h(Steps, null, h(Step, { index: 1, title: t("tutorial.greet.step_title") }, null))
          )
        ),
        footer: h(
          "div",
          { className: "neko-button-group" },
          h(Button, { onClick: props.onBegin, children: t("tutorial.greet.begin") }),
          h(Button, { tone: "default", onClick: props.onSkip, children: t("tutorial.step.skip") })
        ),
      }
    )
  }

  if (phase === "step") {
    // 教练条：**不遮挡** —— 玩家要能点到被圈出来的那个真实元素。
    return h(
      Card,
      {
        className: "rrm-tutorial-coach",
        title: t("tutorial.step.progress", { index: index + 1, total }),
      },
      h(
        Stack,
        { gap: 8 },
        h(
          "div",
          { className: "rrm-tutorial-steps" },
          h(Steps, null, h(Step, { index: index + 1, title: stepTitle }, h(Text, null, stepBody)))
        ),
        tutorial.waiting_gate
          ? h(Alert, {
              tone: "info",
              message: t(String(tutorial.gate_wait_key || "tutorial.gate.wait")),
            })
          : null,
        h(
          "div",
          { className: "neko-button-group" },
          h(Button, { onClick: props.onAdvance, children: t("tutorial.step.next") }),
          h(Button, { tone: "default", onClick: props.onSkip, children: t("tutorial.step.skip") })
        )
      )
    )
  }

  if (phase === "epilogue") {
    return h(
      Modal,
      {
        open: true,
        title: t("tutorial.epilogue.title"),
        onClose: props.onSkip,
        closeOnBackdrop: false,
        closeOnEscape: false,
        size: "md",
        children: h(
          Stack,
          { gap: 12 },
          h(Text, null, t("tutorial.epilogue.body")),
          // 静态合规实体（**兜底**）：台词由 AI 说，这一条是**写死的声明**。
          h(Alert, { tone: "info", message: t("tutorial.epilogue.risk_note") })
        ),
        footer: h(
          "div",
          { className: "neko-button-group" },
          h(Button, { onClick: props.onSkip, children: t("tutorial.epilogue.close") })
        ),
      }
    )
  }

  return null
}

/**
 * P18.0：把被指代的元素**圈出来**并滚进视野。
 *
 * 零危险 CSS：圈是 ``.rrm-tutorial-target``（``outline`` + ``box-shadow``）。
 * 滚动用**宿主自带的** ``useScrollIntoView``。
 *
 * 锚点是 **className 约定**（宿主组件不透传 ``data-*``）：
 * ``market_grid`` ⇒ ``.rrm-anchor-market-grid``。
 */
function useTutorialHighlight(anchor: string | null) {
  const targetRef = useRef<any>(null)
  const scrollToTarget = useScrollIntoView(targetRef)
  useEffect(() => {
    if (!anchor) {
      targetRef.current = null
      return
    }
    const selector = ".rrm-anchor-" + String(anchor).replace(/_/g, "-")
    const node = document.querySelector(selector)
    if (!node) {
      targetRef.current = null
      return
    }
    targetRef.current = node
    node.classList.add("rrm-tutorial-target")
    scrollToTarget({ block: "center", inline: "nearest", behavior: "smooth" })
    return () => {
      node.classList.remove("rrm-tutorial-target")
      targetRef.current = null
    }
  }, [anchor])
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
 * 三步分别讲：① 零花钱/家底/救命钱 + 每日生活费；② 一键梭哈/一键割肉 + 看涨跌图；
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
  /**
   * P23.2：**工具条模式**（局中）。
   *
   * 紧凑档不再渲染那张大卡片，只留一排小药丸
   * （慢 / 标准 / 快 + 自动·手动 + 推进一拍）——
   * 「节奏」这个标题、每天多少分钟、还有几拍到日终，这些说明
   * 在直播看板里全是噪音：真正的信息是**现在是哪一档**。
   */
  compact?: boolean
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
  // ── P23.2：紧凑档 = 一行小工具条 ────────────────────────────────
  if (props.compact) {
    return h(
      Inline,
      { gap: 6, wrap: false, align: "center", className: "rrm-pace-inline" },
      speeds.map((item) =>
        h(Button, {
          key: item.id,
          className:
            item.id === speed
              ? "rrm-speed-tag rrm-speed-tag-on rrm-speed-tag-mini"
              : "rrm-speed-tag rrm-speed-tag-mini",
          disabled: busy,
          onClick: () => onSpeed(item.id),
          children: item.label,
        })
      ),
      h(Button, {
        className: auto
          ? "rrm-pace-toggle rrm-pace-toggle-mini rrm-pace-badge-auto"
          : "rrm-pace-toggle rrm-pace-toggle-mini rrm-pace-badge-manual",
        tone: "default",
        disabled: busy,
        onClick: onToggleAuto,
        // 紧凑档显示的是**当前状态**（自动 / 手动），点一下就切。
        children: auto ? t("panel.pace_auto_badge") : t("panel.pace_manual_badge"),
      }),
      // 手动模式才有【推进一拍】—— 自动模式里给这个按钮会让"自动"变成谎话。
      auto
        ? null
        : h(Button, {
            className: "rrm-pace-advance rrm-pace-advance-mini",
            // ── P11.1：请求在飞时禁用 + 显示"推进中" ──────────────
            // 狂点会被这条禁用态挡掉 ⇒ 宿主不会再收到并发的 action ⇒
            // 不会再有 `Failed to query plugin UI action context`。
            disabled: busy,
            onClick: onAdvance,
            children: busy ? t("panel.pace_advance_busy") : t("panel.pace_advance"),
          })
    )
  }
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

/**
 * P26.0：面板**白屏防御**。
 *
 * 整个面板（连它自己的渲染逻辑）都裹在宿主自带的 ``ErrorBoundary`` 里：
 * 任何一处渲染异常都会被接住 ⇒ 换成「重连中，请稍后」提示块，
 * **绝不允许出现纯白屏**（直播时白屏 = 观众以为断线了）。
 *
 * 主体拆成 ``PanelScreen`` 就是为了这个：ErrorBoundary 放在**外面**，
 * 才能兜住 ``PanelScreen`` **自己**渲染时抛的异常；
 * 放在里面的话只能兜住子组件的异常。
 */
export default function Panel(props: PluginSurfaceProps<State>) {
  return h(
    ErrorBoundary,
    {
      fallback: (error: any, reset: () => void) => renderPanelFallback(props.t, error, reset),
    },
    h(PanelScreen, props)
  )
}

/** P26.0：面板主体（原来那个 ``Panel`` 的实现，逻辑一行没改）。 */
function PanelScreen(props: PluginSurfaceProps<State>) {
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
  //
  // P23.1：改成**按标的存一张表** —— 以前只存最后拉的那一只，
  // 于是「A → B → A」切回来时 A 的图没了，得重拉一次。
  const [klineDetailMap, setKlineDetailMap] = useState<Record<string, any>>({})
  // ── P23.1：K 线有**自己的一份选择**，不跟交易台共用 ──────────────
  //
  // 真机事故：之前 K 线下拉框和「买哪只」的下拉框共用 ``selectedStock``，
  // 于是玩家在交易台挑了只标的 ⇒ K 线被切到那只 ⇒ 而载荷只带前 20 只的
  // K 线 ⇒ 那只没有数据 ⇒ 大 K 线直接塌掉。
  //
  // 现在两边彻底分开：交易台挑标的**不再影响** K 线看哪只。
  const [klineStockPick, setKlineStockPick] = useLocalState<string>(
    "rrm.kline_stock",
    ""
  )
  // 已经自动拉过哪只了（别重复拉）。
  const klineAutoFetched = useRef("")
  // ── P23.1：插件**离线**（停了）的状态 ──────────────────────────
  //
  // 真机事故：插件已经停了，面板还照常渲染按钮，玩家一按就弹出一堆
  // 「插件未运行」的红色警告。现在认准那个 code ⇒ 面板整体换成
  // 一块「插件已停止」的提示，**只提示一次**。
  const [pluginOffline, setPluginOffline] = useState(false)
  const offlineNotified = useRef(false)
  // ── P5.5 改动 4：天桥三屏（1 → 2 → 3，间隔自动推进）─────────────
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
  // ── P7.2：濒临破产档「数字偶尔闪红」（**JS 定时器**，不用 CSS 动画）──
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
  // ── P21.0：三档 UI 密度（渐进式简化）──────────────────────────────
  //
  // 只影响**显示**：底层的计算、费率、判定一个字都没动。
  //
  // P28.0：**街机档**（classic_arcade）的**极限删减** ——
  // 屏幕上只留超大 K 线 + 掏钱买 + 割肉卖 + 当前的现钱。
  //
  // 判定口径 = **档位 + 后端显式字段**（两者取「或」）：
  // · ``minimalUi``：街机档在 UI 密度上本来就是 minimal（P21.0 口径）；
  // · ``arcade_simple``：后端在载荷里**显式**给的顶层布尔
  //   （true 当且仅当 session.mode == "classic_arcade"）。
  //
  // 老载荷没有 ``arcade_simple`` ⇒ 靠 ``minimalUi`` 兜底（口径一致）；
  // 载荷缺 ``mode`` ⇒ 靠 ``arcade_simple`` 兜底。两边都缺 ⇒ 按 full 档走。
  //
  // （这段必须排在**教程锚点之前** —— 街机档没有水位条，
  //  教程的「看水位」那一步不许再圈东西。）
  const uiLevel = uiLevelFor(String(state.mode || ""))
  const minimalUi = uiLevel === "minimal" || Boolean((state as any).arcade_simple)
  const arcadeSimple = minimalUi || Boolean((state as any).arcade_simple)
  // ── P18.0：分档次动态教程 ──────────────────────────────────────
  // 载荷里只有**功能性 UI 文案的 i18n key**；台词由 Yui 在面板外说。
  const tutorial = (state.tutorial || {}) as NonNullable<State["tutorial"]>
  const tutorialPhase = String(tutorial.phase || "idle")
  // P28.0：街机档把「持仓台账」和「资金水位条」**都藏了** ⇒ 教程若锚到它们
  // 就圈不到任何东西 ⇒ 统一改指**顶部概览**（总资产 / 盈亏 / 现钱都在那儿）。
  //
  // 这张表是**兜底**：后端就算哪天又把隐藏锚点发给街机档，面板也会自己改道，
  // 不会出现「教程圈了一个不存在的东西」。
  const rawTutorialAnchor = tutorial.step ? String(tutorial.step.anchor || "") : ""
  const ARCADE_HIDDEN_ANCHORS: Record<string, string> = {
    gauge: "overview",
    position_grid: "overview",
  }
  const tutorialAnchor = arcadeSimple
    ? ARCADE_HIDDEN_ANCHORS[rawTutorialAnchor] || rawTutorialAnchor
    : rawTutorialAnchor
  const tutorialWaiting = Boolean(tutorial.waiting_gate)
  // 上课时才圈目标（开场 / 结课那两个全屏时刻不需要圈）。
  //
  // **等门槛事件的那一步不圈** —— 课还没开课，先圈会误导玩家
  // （而且门槛打开前后锚点相同，不这样处理的话高亮压根不会重新生效）。
  useTutorialHighlight(
    tutorialPhase === "step" && !tutorialWaiting ? tutorialAnchor : null
  )
  // P14.0：【命运挑战】的二级档位选择（入门 / 进阶 / 地狱）。
  const [challengeOpen, setChallengeOpen] = useState(false)
  // 「只自动弹一次」：**本次挂载**只试一次（面板每 3 秒轮询，不能反复弹）。
  const guideAutoTried = useRef(false)
  useEffect(() => {
    if (guideAutoTried.current) return
    if (!onboarding.auto_open) return
    guideAutoTried.current = true
    setGuideOpen(true)
  }, [onboarding.auto_open])

  // ── P20.0：玩家自报名号（**戏名**）───────────────────────────────
  //
  // 后端把玩家上次的自报名号放进 `state.player_name`（空闲态回填）。
  // 用户改过之后就不再被后端覆盖（`nameTouched`）。
  //
  // **防呆在前端先做一遍**（去换行、限长 12），后端**还会再洗一遍** ——
  // 面板永远不可信。
  const [nameDraft, setNameDraft] = useState<string>("")
  const [nameTouched, setNameTouched] = useState(false)
  useEffect(() => {
    if (nameTouched) return
    const incoming = String(state.player_name || "")
    if (incoming) setNameDraft(incoming)
  }, [state.player_name, nameTouched])
  const onNameInput = (raw: string) => {
    setNameTouched(true)
    const cleaned = String(raw === undefined || raw === null ? "" : raw)
      .replace(/[\r\n\t]/g, " ")
      // P21.0：限 **6 个字** —— 太长会撑爆面板上的标题栏。
      // 后端还会再洗一遍（``name_easter_eggs.MAX_NAME_LENGTH``）。
      .slice(0, 6)
    setNameDraft(cleaned)
  }
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
  // P26.0：请求序号 —— 超时之后旧请求可能**晚到**，
  // 它不许再解锁 / 改状态（那时锁可能已经是下一个请求的了）。
  const requestSeqRef = useRef(0)

  /**
   * 走锁的 action 调用。
   *
   * * 已经有请求在飞 ⇒ **静默丢弃**（只弹一条温和的 info 提示，
   *   **绝不**红色警告制造恐慌）；
   * * 返回 action 的载荷（``Ok({...})`` 里的字典），拿不到就 ``null``。
   */
  /**
   * P23.1：插件**离线**时的一次性提示。
   *
   * 真机事故：插件停了，面板还照常渲染按钮，玩家一按就弹出一堆
   * 「插件未运行」的红色警告（截图 3）。现在：
   * · 认准宿主的 ``PLUGIN_NOT_RUNNING`` ⇒ 面板整体停摆（换成提示块）；
   * · 黄色（warning）提示**只报一次**，不再一条一条地刷。
   */
  const markPluginOffline = () => {
    setPluginOffline(true)
    if (offlineNotified.current) return
    offlineNotified.current = true
    showToast(t("panel.offline_toast"), "warning")
  }

  /**
   * P23.1：点【重试连接】—— 插件重新启动后，按一下就能回到正常面板。
   */
  const retryPlugin = async () => {
    try {
      await props.api.refresh()
      setPluginOffline(false)
      offlineNotified.current = false
    } catch (_error) {
      showToast(t("panel.offline_still"), "warning")
    }
  }

  /**
   * P26.0：后台悄悄把上下文拉回来。
   *
   * **不阻塞**解锁，也不弹 Toast —— 拉得到就拉，拉不到就算了。
   */
  const refreshQuietly = async () => {
    try {
      await withTimeout(props.api.refresh(), ACTION_TIMEOUT_MS)
    } catch (_error) {
      // 拉不到就算了。
    }
  }

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
    // P26.0：领一张**请求票**。超时之后旧请求可能晚到，
    // 那时锁可能已经是下一个请求的了 —— 过期的一律不碰状态。
    const ticket = ++requestSeqRef.current
    const stale = () => ticket !== requestSeqRef.current
    try {
      const result = await withTimeout(
        props.api.call(action, args, {
          userInitiated: options?.userInitiated !== false,
        }),
        ACTION_TIMEOUT_MS
      )
      if (stale()) return null
      // 刷新**也**纳入同一道闸：refresh 挂起一样会把面板锁死。
      await withTimeout(props.api.refresh(), ACTION_TIMEOUT_MS)
      if (stale()) return null
      return result
    } catch (error: any) {
      if (stale()) return null
      // P26.0：超时 —— **先解锁**，再弹黄色提示。
      // 顺序很重要：观众看到 Toast 的那一刻，面板必须已经能继续用。
      if (isActionTimeoutError(error)) {
        busyRef.current = false
        setBusy(false)
        showToast(t("panel.action_timeout"), "warning")
        void refreshQuietly()
        return null
      }
      // P23.1：插件停了 ⇒ **不刷红色警告**，只提示一次然后面板停摆。
      if (isPluginOfflineError(error)) {
        markPluginOffline()
        return null
      }
      showToast(String((error && error.message) || error), "danger")
      // 失败**也要**把上下文拉回来 —— 面板不能停在旧状态
      // （比如这一局已经结束了，屏幕上的按钮全是死的）。见 P7.4 的 resync。
      try {
        await withTimeout(props.api.refresh(), ACTION_TIMEOUT_MS)
      } catch (_error) {
        // 拉不到就算了。
      }
      return null
    } finally {
      if (!stale()) {
        busyRef.current = false
        setBusy(false)
      }
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
   * 【执行买入】：按滑块（或直输框反推出来的）仓位买入。
   *
   * 走 ``callAction`` ⇒ **纳入 P11.1 的全局请求锁**（狂点只会被静默丢弃，
   * 不会并发打爆宿主那次 ``get_ui_context``）。
   */
  const executeBuy = async () => {
    if (buyAmount <= 0) {
      showToast(t("visual.no_money"), "warning")
      return
    }
    // P21.0：第一次成交 ⇒ 飘出新手兜底提示（没动滑块就直接买也算）。
    triggerGhostHint()
    await callAction("buy", { stock_id: effectiveStockId, amount: buyAmount })
    clearTradeDrafts()
    // P14：这只已经买过了 ⇒ 它那份"计划"作废（不然台账会重复计算）。
    // 别的标的的计划留着 —— 现金变少之后它们会**自动回缩**。
    setPlanBudgetMap((prev: Record<string, number>) => {
      const next = { ...(prev || {}) }
      delete next[effectiveStockId]
      return next
    })
  }

  /** 【执行卖出】：按滑块（或直输框反推出来的）仓位卖出。 */
  const executeSell = async () => {
    if (sellAmount <= 0) {
      showToast(t("visual.nothing_to_sell"), "warning")
      return
    }
    // P21.0：第一次成交 ⇒ 飘出新手兜底提示（没动滑块就直接卖也算）。
    triggerGhostHint()
    await callAction("sell", { stock_id: effectiveSellStockId, amount: sellAmount })
    clearTradeDrafts()
  }

  /**
   * 【执行做空】：按选好的**倍率** + **保证金**开一笔空头（P27.0）。
   *
   * 走 ``callAction`` ⇒ 纳入 P11.1 的全局请求锁（狂点只会被静默丢弃）。
   * 爆仓、盯市、结算全在**插件层**（``short_book.py``）—— 引擎层一行没动。
   */
  const executeShort = async () => {
    const margin = Number(shortMarginDraft)
    if (!Number.isFinite(margin) || margin < shortMinMargin) {
      showToast(t("panel.short_bad_margin"), "warning")
      return
    }
    await callAction("short", {
      stock_id: effectiveShortStockId,
      margin: margin,
      leverage: effectiveShortLeverage,
    })
  }

  /** 【买报纸】：多买一次分析机会（P27.0 教程里「后面自己买报纸」的兑现）。 */
  const buyReport = async () => {
    await callAction("buy_report", {})
  }
  // ── P18.0：教程的三个动作（开始 / 下一步 / 跳过）──────────────────
  // **同步关界面**：后端成不成功都不该把玩家卡住。
  const tutorialCall = (action: string) => {
    try {
      props.api
        .call(action, {}, { userInitiated: true })
        .then(() => {
          try {
            props.api.refresh()
          } catch (_error) {
            // 刷新失败没关系：面板每 3 秒自己会轮询一次。
          }
        })
        .catch(() => undefined)
    } catch (_error) {
      // 连调用都抛出来了：教程只是引导，失败也不能挡住玩家。
    }
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

  // ── P23.0：【耻辱柱】—— 拉历史破产记录 ──────────────────────────────
  const openShame = async () => {
    const result = await callAction("shame", {})
    if (result === null) return
    const rows = Array.isArray(result.rows) ? result.rows : []
    setShameRows(rows)
    setShameOpen(true)
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

  // ── P7.2：濒临破产档「数字偶尔闪红」 ────────────────────────────────
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

  // ── P14.0：资金水位条的**危机闪烁** ──────────────────────────────
  // 现金比明天的生活费还少 ⇒ 整根水位条变红并轻微闪烁。
  // 同样**不用 CSS 动画**：JS 定时器切颜色（跟上面濒临破产数字同款）。
  const nextBill = Math.max(0, Number(state.next_bill || 0))
  const cashNow = Math.max(0, Number(state.cash || 0))
  const cashCrisis = nextBill > 0 && cashNow < nextBill
  const [gaugeAlarm, setGaugeAlarm] = useState(false)
  useEffect(() => {
    if (!cashCrisis) {
      setGaugeAlarm(false)
      return
    }
    let hideTimer: any = 0
    // P23.0：900ms → 620ms（红线深浅红切得更"躁动"一点）。
    const timer = setInterval(() => {
      setGaugeAlarm(true)
      hideTimer = setTimeout(() => setGaugeAlarm(false), 240)
    }, 620)
    return () => {
      clearInterval(timer)
      clearTimeout(hideTimer)
    }
  }, [cashCrisis])

  // ── P23.0：濒危发病 ①：**资金数字随机抖动** ─────────────────────────
  //
  // 现金跌破饭钱红线 ⇒ 那个数字开始"手抖"。
  // 只切 ``transform``（``translate``）—— 不用 CSS 动画、不用 ``filter``。
  const [cashShake, setCashShake] = useState({ x: 0, y: 0 })
  useEffect(() => {
    if (!cashCrisis) {
      setCashShake({ x: 0, y: 0 })
      return
    }
    const timer = setInterval(() => {
      setCashShake({
        x: Math.round(Math.random() * 8 - 4),
        y: Math.round(Math.random() * 6 - 3),
      })
    }, CRISIS_SHAKE_INTERVAL_MS)
    return () => {
      clearInterval(timer)
      setCashShake({ x: 0, y: 0 })
    }
  }, [cashCrisis])

  // ── P23.0：濒危发病 ②：**全屏边缘红光** ─────────────────────────────
  //
  // ``box-shadow: inset``（不需要定位）；根节点自己补了 ``min-height: 100vh``，
  // 所以这道光真的铺到视口边缘。
  // **合规**：单次 ≤ 200ms、周期 ≥ 1.2s、α ≤ 0.35（见常量区）。
  const [crisisGlow, setCrisisGlow] = useState(false)
  useEffect(() => {
    if (!cashCrisis) {
      setCrisisGlow(false)
      return
    }
    let hideTimer: any = 0
    const timer = setInterval(() => {
      setCrisisGlow(true)
      hideTimer = setTimeout(() => setCrisisGlow(false), CRISIS_GLOW_ON_MS)
    }, CRISIS_GLOW_CYCLE_MS)
    return () => {
      clearInterval(timer)
      clearTimeout(hideTimer)
    }
  }, [cashCrisis])

  // ── P23.0：危机态的**根节点类名**（全屏边缘红光的挂载点）────────────
  //
  // 平时就是 ``rrm-root``；危机态临时多挂一个 ``rrm-crisis-glow``。
  const rootClass = crisisGlow ? "rrm-root rrm-crisis-glow" : "rrm-root"

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
  // ── P23.2：「前排高关注区 / 后排常规区」─────────────────────────────
  //
  // 行情方块**全量**铺（卡内可纵向滚动），但**顺序**重排过：
  // 爆炸标签 → 波动率大 → 当天振幅大 的排在最前面（妖股先看），
  // 稳定 / 中性的票沉到后排。
  //
  // 直播看板的第一诉求：观众第一眼看到的必须是最容易出节目效果的那几只。
  const sortedStocks = sortStocksByHotness(stocks)
  // 防御性上限（载荷不可信）：引擎的 initial_symbols = 20，60 足够宽松。
  const visibleStocks = sortedStocks.slice(0, 60)
  const hiddenStockCount = Math.max(0, stocks.length - visibleStocks.length)
  // ── P21.0：三档 UI 密度（渐进式简化）──────────────────────────────
  //
  // 只影响**显示**：底层的计算、费率、判定一个字都没动。
  //
  // （档位 / 街机档判定见上面 —— 必须排在教程锚点之前。）
  //
  // 街机档**不是** full 档：哪怕载荷里没带 mode，也别把全套件端上来。
  const fullUi = uiLevel === "full" && !arcadeSimple
  const guardedUi = uiLevel === "guarded"
  // 前两关的持仓只铺前 3 只（其余折成一行字）—— P16.0 的既有口径。
  const simpleUi = !fullUi
  // ── 教程例外：**教程点到哪，那块就必须看得见** ───────────────────
  //
  // 教程圈到的那一块强制显示（P18.0 踩过一次同样的坑：
  // 锚点 class 漏了，教程圈了半天没圈到东西）。
  //
  // P28.0：**街机档没有例外** —— 街机档只留 K 线 / 两键 / 现钱，
  // 连教程也不许把水位条叫回来（街机档的教程锚点根本不给「水位」）。
  // 街机档连**教程例外**都不给：水位条在街机档永远不回来。
  const tutorialNeeds = (anchor: string): boolean => !arcadeSimple && tutorialAnchor === anchor
  // 水位条：入门起恢复（开始教生存了）；街机默认不显示。
  const showGauge = fullUi || guardedUi || tutorialNeeds("gauge")
  // 连击徽章：街机与入门都不给（纯爽局配件，前两关先教活下来）。
  const showCombo = fullUi
  // 集中度警告 / 计划台账：只有后面三档才玩。
  const showConcentration = fullUi
  const showPlanLedger = fullUi
  // 家道徽标（丰盈 / 断供 / 濒临破产）与「距离强制平仓线」：
  // 街机档没有账单，这两样**恒定没意义**，纯噪音 ⇒ 藏起来。
  const showFamilyBadge = !minimalUi
  const showKillLine = !minimalUi
  // 明日账单提示：街机档压根没有账单 ⇒ 不显示。
  const showBillAlert = !minimalUi
  // ── P23.2：持仓也按**同一套热度**排（妖股持仓在前）──────────────
  // 热度相同时市值大的在前；再相同按 id 定序（顺序稳定，不跳版）。
  const rankedPositions = sortPositionsByHotness(positions)
  const shownPositions = simpleUi
    ? rankedPositions.slice(0, SIMPLE_POSITION_LIMIT)
    : rankedPositions
  const hiddenPositionCount = Math.max(0, rankedPositions.length - shownPositions.length)
  const news = state.news_today || []
  // P26.0：群友随机事件（面板只拿白名单字段；内参正文不在里面）。
  const community = state.community_events || []
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
    // P23.1：插件停了 ⇒ 黄色提示**只报一次** + 面板整体停摆，
    // 绝不再刷一堆红色警告（截图 3 的事故）。
    if (isPluginOfflineError(error)) {
      markPluginOffline()
      return
    }
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
  //
  // P23.1：**选择独立于交易台**（用 ``klineStockPick``，不再跟 ``selectedStock``
  // 共用），并且默认落在**载荷里真的带 K 线**的那只 ——
  // 否则玩家一眼看到的可能是一只"没数据"的标的，大图直接是空的。
  const klineStockId =
    klineStockPick && stockOptions.some((option) => option.value === klineStockPick)
      ? klineStockPick
      : klineIds.length > 0
        ? klineIds[0]
        : effectiveStockId
  const klineStock = stocks.find((stock) => stock.id === klineStockId)
  const bundledKline = (klineStock && klineStock.kline) || []
  const fetchedKline = (klineDetailMap[klineStockId] || {}).kline || []
  const klinePoints = bundledKline.length > 0 ? bundledKline : fetchedKline

  // ── P23.1：选中的标的没带 K 线 ⇒ **自动拉一次** ──────────────────
  //
  // 以前得手动点「拉这只的K线」才看得到别的标的；现在选中即自动拉，
  // 玩家不会对着一个空框发呆。
  //
  // 直接调宿主的调用口，而不是走 ``callAction``：``callAction`` 会抢全局请求锁、
  // 还会弹 toast —— 自动拉取不该抢锁，也不该打扰人。
  //
  // （注：官方 TSX 校验器扫的是**全文**，注释里写「宿主调用口」这种裸词也会
  // 被当成「用了全局 api 对象」而报错 —— 注释里一律别写那个裸词。）
  useEffect(() => {
    if (!klineStockId) return
    if (bundledKline.length > 0) return
    if ((klineDetailMap[klineStockId] || {}).kline) return
    if (klineAutoFetched.current === klineStockId) return
    klineAutoFetched.current = klineStockId
    try {
      const pending = props.api.call(
        "kline",
        { stock_id: klineStockId },
        { userInitiated: false }
      )
      if (pending && typeof pending.then === "function") {
        pending
          .then((result: any) => {
            if (result && result.stock_id) {
              const key = String(result.stock_id)
              setKlineDetailMap((prev: Record<string, any>) => ({
                ...prev,
                [key]: result,
              }))
            }
          })
          .catch(() => undefined)
      }
    } catch (_error) {
      // 拉不到就算了（插件可能已经停了）。
    }
  }, [klineStockId, bundledKline.length, klineDetailMap, props.api])

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
  // P7.2：总资产按档位换名字（零花钱 / 家底 / 救命钱）。
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
  // ── P20.0：玩家自报名号 + 命中的名号（**局中与结算页显示**）────────
  //
  // 空闲态**不显示**（那儿有输入框，名字已经看得见了）。
  // `label_key` 由后端给（`panel.name_egg_*`），面板自己查表显示徽标 ——
  // 面板**不解释**这个名号意味着什么（那是彩蛋引擎的事）。
  const playerName = String(state.player_name || "")
  const nameEgg = state.name_egg || null
  const nameEggLabel = nameEgg && nameEgg.label_key ? t(String(nameEgg.label_key)) : ""
  const showNameBadge = Boolean(playerName) && state.stage !== "idle"
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
  // 濒临破产档：数字**偶尔闪红**（用 JS 定时器切颜色，不用 CSS 动画）。
  const flashColor = flashOn ? "var(--danger)" : undefined

  // ── P16.0：盈利数字「跳动」────────────────────────────────────────
  // 街机档专用：**不用 CSS 动画**（0.8.0 的崩溃根因），用 JS 定时器把数字
  // 从旧值**滚**到新值，滚完再补一下弹跳 —— 让「赚到钱」这件事被看见。
  const combo = state.combo || null
  const comboEnabled = Boolean(combo && combo.enabled)
  const comboStreak = comboEnabled ? Math.max(0, Number(combo.streak || 0)) : 0
  const comboBest = comboEnabled ? Math.max(0, Number(combo.best || 0)) : 0
  const pnlShownRef = useRef(pnl)
  const prevPnlRef = useRef(pnl)
  const [pnlShown, setPnlShown] = useState(pnl)
  const [pnlPop, setPnlPop] = useState(false)
  // ── P23.0：老虎机闪光（赚绿 / 亏红）────────────────────────────────
  const [pnlFlash, setPnlFlash] = useState<"" | "up" | "down">("")
  useEffect(() => {
    const target = pnl
    const from = pnlShownRef.current
    // P23.0：**不再看街机档开关** —— 这版的诉求是「极简纯爽」，
    // 数字滚动的刺激感在**所有档位**都要有。
    if (Math.abs(target - from) < 0.005) {
      pnlShownRef.current = target
      setPnlShown(target)
      return
    }
    const started = Date.now()
    // P23.0：520ms → 420ms，帧率 40ms → 16ms（四次缓出更"老虎机"）。
    const duration = 420
    const timer = setInterval(() => {
      const ratio = Math.min(1, (Date.now() - started) / duration)
      const eased = 1 - Math.pow(1 - ratio, 4)
      const next = from + (target - from) * eased
      pnlShownRef.current = next
      setPnlShown(next)
      if (ratio >= 1) {
        clearInterval(timer)
        pnlShownRef.current = target
        setPnlShown(target)
      }
    }, 16)
    return () => clearInterval(timer)
  }, [pnl])
  useEffect(() => {
    const previous = prevPnlRef.current
    prevPnlRef.current = pnl
    if (Math.abs(pnl - previous) < 0.005) return
    if (pnl > previous) {
      setPnlPop(true)
      const popTimer = setTimeout(() => setPnlPop(false), 300)
      // P23.0：盈亏方向变了 ⇒ 闪一下（赚绿 / 亏红）。
      //
      // **为什么红绿跟涨跌色反着**：这里说的是"赚没赚"，不是"价格涨没涨"——
      // 两套语义（--success / --danger vs --up / --down）不许混。
      setPnlFlash("up")
      const flashTimer = setTimeout(() => setPnlFlash(""), 520)
      return () => {
        clearTimeout(popTimer)
        clearTimeout(flashTimer)
      }
    }
    setPnlFlash("down")
    const flashTimer = setTimeout(() => setPnlFlash(""), 520)
    return () => clearTimeout(flashTimer)
  }, [pnl])

  // ── P13：仓位控制（连续滑块 + 直输框）+ 实时预览 ──────────────────
  //
  // **为什么要彻底去量子化**：仓位被量化成 25/50/75/100 四档之后，玩家
  // 能做的只有"选一档"，"再压一点点""就差这一股"这种真实存在的动作
  // 做不出来。现在滑块是 **1% 步进的连续** 的，另外可以**直接输金额或
  // 股数** —— 系统反推仓位比例。"把最后几块钱也砸进去"应该做得到。
  //
  // **预览怎么算准**：手续费率是**引擎那一套**（``state.fees``，只读），
  // 所以"预计花费"和插件真正扣的钱**一致**（不是 0.995 那种经验折算）。
  //
  // **唯一的事实来源是仓位比例**：直输框只是**反推**出比例，真正成交的
  // 数量永远由比例算出来 ⇒ 拖滑块和手输不会打架、不会各算各的。
  // ── P14.0：计划买入台账（多标的）+ 买入/卖出**两条**独立仓位 ─────────
  //
  // **为什么要拆成两条**：P13 只有一个"仓位"，买入和卖出共用它 ——
  // 拖一下就同时改了"买多少"和"卖多少"，本来就该分开。
  // 更要紧的是**买入**那条要多一层约束：玩家可以给**好几只**标的排计划，
  // 一旦计划总额超过可用现金，**后拉的那条滑条必须被截断** ——
  // 那股"拉扯"的阻力感，就是在告诉玩家他的极限在哪。
  //
  // 台账的键是**标的 id**，值是**打算投入的绝对金额（元）**。
  // 用绝对金额而不是百分比：现金变少时这些金额**相对变重**，
  // 滑块会**自动回缩** —— 玩家看到的极限永远是真的。
  const cash = Math.max(0, Number(state.cash || 0))
  const [planBudgetMap, setPlanBudgetMap] = useLocalState<Record<string, number>>(
    "rrm.plan_budget",
    {}
  )
  const [sellPositionPct, setSellPositionPct] = useLocalState<number>(
    "rrm.sell_position_pct",
    100
  )
  // ── P27.0：做空（倍率 + 保证金）────────────────────────────────────
  //
  // 倍率表 / 上限 / 最小保证金**一律从载荷里拿**（``short_options``）——
  // 面板不硬编码，后端改口径它自己就跟着变。
  const shortOptions = state.short_options || {}
  const shortLeverages = (shortOptions.leverages || []).map((item) => Number(item))
  const shortMinMargin = Number(shortOptions.min_margin || 1)
  const shortLeverageOptions = shortLeverages.map((item) => ({
    value: String(item),
    label: `${item}x`,
  }))
  const [shortLeverage, setShortLeverage] = useLocalState<string>(
    "rrm.short_leverage",
    String(Number(shortOptions.default_leverage || 10))
  )
  const [shortStock, setShortStock] = useLocalState<string>("rrm.short_stock", "")
  const [shortMarginDraft, setShortMarginDraft] = useLocalState<string>(
    "rrm.short_margin",
    ""
  )
  // 本地记忆的倍率可能已经不在倍率表里了（后端改过口径）⇒ 回落默认值。
  const effectiveShortLeverage =
    shortLeverages.indexOf(Number(shortLeverage)) >= 0
      ? Number(shortLeverage)
      : Number(shortOptions.default_leverage || 10)
  const effectiveShortStockId =
    shortStock && stockOptions.some((option) => option.value === shortStock)
      ? shortStock
      : effectiveStockId
  const shortPositions = state.short_positions || []
  // ── P27.0：玩家 vs 猫娘 Yui 的资产曲线（纯表现层）──────────────────
  const equityCurve = state.equity_curve || {}
  const equityPlayer = equityCurve.player || []
  const equityYui = equityCurve.yui || []
  // ── P27.1：顶部概览里那句「你 vs Yui」───────────────────────────────
  //
  // Yui 的身家取她那条曲线的**最后一个点**（后端每 tick 都在推）。
  // 没有她的数据时就说实话（还没开打），不编一个数字出来。
  const yuiAssets = equityYui.length > 0 ? Number(equityYui[equityYui.length - 1] || 0) : 0
  const yuiGap = totalAssets - yuiAssets
  const yuiCompareText =
    yuiAssets > 0
      ? yuiGap >= 0
        ? t("panel.overview_ahead", { gap: Math.round(yuiGap) })
        : t("panel.overview_behind", { gap: Math.round(Math.abs(yuiGap)) })
      : t("panel.overview_vs_yui", {
          player: Math.round(totalAssets),
          yui: Math.round(yuiAssets),
        })
  // ── P23.0：操作台的「精调」折叠 ─────────────────────────────────────
  //
  // 这一版的核心是**极简纯爽**：屏幕上最该有的就是巨幅 K 线 + 两个巨号按钮。
  // 滑块、直输框、实时预览、台账……这些**精调件**统统折叠起来，
  // 想改仓位再点开那一行。
  //
  // 折叠**不丢状态**：仓位比例存在 ``useLocalState`` 里，
  // 折叠时那两个巨号按钮**照旧**按这个比例成交。
  const [tradeFineTuneOpen, setTradeFineTuneOpen] = useState(false)
  // ── P23.2：【更多】弹窗（名册 / 新闻 / 猫娘状态 / 弹幕）──────────
  //
  // 局中面板只留核心：名字、天数、钱、K 线、按钮。
  // 剩下的那些收进一个弹窗 —— **默认关着**，直播时一个键就能叫出来。
  const [extraOpen, setExtraOpen] = useState(false)
  // ── P23.0：【耻辱柱】的弹窗 ────────────────────────────────────────
  const [shameOpen, setShameOpen] = useState(false)
  const [shameRows, setShameRows] = useState<any[]>([])
  // ── P23.0：Yui 介入横幅 ────────────────────────────────────────────
  //
  // 面板**没有状态订阅**（宿主 ``HostedApi`` 只有 call / parseDocument / refresh），
  // 靠 3 秒轮询拿到 ``state.yui.seq``。序号变了 ⇒ 猫娘刚动过手 ⇒ 亮横幅。
  const yuiBlock = state.yui || null
  const yuiSeq = Number(yuiBlock?.seq || 0)
  const yuiMode = String(yuiBlock?.mode || "assist")
  // ── P24.0：Yui 的【话痨级别】（安静 / 正常 / 话痨）──────────────────
  // 后端每 tick 把它算成「每隔几个 tick 开一次回合」，这里只是回显。
  const yuiTalkLevel = String(yuiBlock?.talk_level || "normal")
  const [yuiBanner, setYuiBanner] = useState<any>(null)
  const [yuiBannerHot, setYuiBannerHot] = useState(false)
  const yuiSeenRef = useRef(0)
  // ── P23.0：Yui 介入横幅的**出现与消退** ──────────────────────────────
  //
  // 面板靠 3 秒轮询拿到 ``state.yui.seq``；序号变了 ⇒ 猫娘刚动过手。
  //
  // **这两个 effect 必须待在 yuiSeq / yuiBanner 的声明之后** ——
  // 依赖数组是在渲染期求值的，声明在前 ⇒ 运行时直接 TDZ 报错、面板白屏。
  useEffect(() => {
    if (yuiSeq <= 0 || yuiSeq === yuiSeenRef.current) return
    yuiSeenRef.current = yuiSeq
    const last = (state.yui || {}).last || null
    if (!last) return
    setYuiBanner(last)
    const timer = setTimeout(() => setYuiBanner(null), 8000)
    return () => clearTimeout(timer)
  }, [yuiSeq])

  // ── P23.0：Yui 横幅的「呼吸」高光（JS 定时器，**不用 CSS 动画**）──────
  useEffect(() => {
    if (!yuiBanner) {
      setYuiBannerHot(false)
      return
    }
    const timer = setInterval(() => setYuiBannerHot((prev) => !prev), 520)
    return () => clearInterval(timer)
  }, [yuiBanner])
  // ── P21.0：新手兜底的「幽灵提示」──────────────────────────────────
  //
  // 很多主播会直接跳过教程开干 ⇒ 他第一次动手时，两眼一抹黑。
  // 所以：他**第一次动滑块 / 第一次成交**时，操作区旁边飘出一行小字
  //（「不会玩可以点上面的【呼叫猫娘】」）。
  //
  // **这是静态 UI 文案，不是 Yui 台词** —— 台词仍然 100% 由她生成
  // （P13 契约）。这里只负责**指路**：把人送到能问人的地方去。
  const [ghostHintOn, setGhostHintOn] = useState(false)
  const [ghostHintIn, setGhostHintIn] = useState(false)
  const ghostHintUsed = useRef(false)
  // 教程正在教 ⇒ 有人盯着，不需要这行提示。
  const tutorialCoaching = Boolean(tutorial.active)
  const triggerGhostHint = () => {
    if (ghostHintUsed.current || tutorialCoaching) return
    ghostHintUsed.current = true
    setGhostHintOn(true)
  }
  // 「飘出」= 先低一点点，再抬上来（**JS 定时器 + transform**，
  // 不用 CSS 动画 —— 0.8.0 的崩溃根因之一）。
  useEffect(() => {
    if (!ghostHintOn) {
      setGhostHintIn(false)
      return
    }
    const rise = setTimeout(() => setGhostHintIn(true), 30)
    const fade = setTimeout(() => setGhostHintOn(false), 9000)
    return () => {
      clearTimeout(rise)
      clearTimeout(fade)
    }
  }, [ghostHintOn])
  const planBudget = planBudgetMap || {}
  // 其它标的已经占用的额度（不含当前标的）。
  //
  // ── P21.0：前两关**不玩多标的计划** ⇒ 这里恒为 0 ──────────────────
  //
  // 台账藏起来了，台账占掉的额度也必须一起消失 ——
  // 否则玩家会撞上一个「滑条莫名其妙拉不满」的鬼现象，
  // 而他根本看不到原因（台账不在面板上）。
  //
  // 换句话说：前两关的买入滑条上限 = **全部现金**，就是一个纯粹的
  // 「这回掏多少」。
  const othersBudget = showPlanLedger
    ? Object.keys(planBudget).reduce((sum, key) => {
        if (key === effectiveStockId) return sum
        return sum + Math.max(0, Number(planBudget[key] || 0))
      }, 0)
    : 0
  // **本轮上限**：现金 − 别人占掉的 ⇒ 后拉动的滑条被它截断。
  const capBudget = Math.max(0, cash - othersBudget)
  const capPct = cash > 0 ? Math.min(100, (capBudget / cash) * 100) : 0
  // 当前标的的**有效**计划（超出上限的部分直接砍掉）。
  const currentPlanBudget = Math.max(
    0,
    Math.min(capBudget, Number(planBudget[effectiveStockId] || 0))
  )
  // 比例**允许是小数**：直输"买 10 股"折出来的比例可能是 33.33%，
  // 取整会让玩家要的 10 股变成 9 股 —— 悄悄改玩家的单子，最糟。
  const buyPositionPct = cash > 0 ? Math.min(100, (currentPlanBudget / cash) * 100) : 0
  const sellPct = Math.max(0, Math.min(100, Number(sellPositionPct || 0)))
  // 轨道填充比例：满格 = 本轮**上限**（上限被别的标的占掉时，轨道会变短）。
  const buyFillPct =
    capPct > 0 ? Math.max(0, Math.min(100, (buyPositionPct / capPct) * 100)) : 0
  // 买入仓位 ⇒ 写进当前标的的**计划买入**，并受全局上限**截断**。
  const setPositionPct = (value: number) => {
    const wanted = Math.max(0, Number(value) || 0)
    const budget = Math.min((cash * wanted) / 100, capBudget)
    setPlanBudgetMap((prev: Record<string, number>) => ({
      ...(prev || {}),
      [effectiveStockId]: budget,
    }))
    // P21.0：第一次动买入滑块 ⇒ 飘出新手兜底提示。
    triggerGhostHint()
  }
  const buyRate = Number(state.fees?.buy_rate || 0)
  const sellRate = Number(state.fees?.sell_rate || 0)
  const stampRate = Number(state.fees?.stamp_tax_rate || 0)

  // 浮点保险：比例 → 数量 的来回换算会带上 1e-14 量级的误差，直接
  // Math.floor 会把"明明是 1 股"算成 0 股。落在整数附近的就按整数算。
  const robustFloor = (value: number) => {
    if (!Number.isFinite(value)) return 0
    const nearest = Math.round(value)
    if (Math.abs(value - nearest) < 1e-6) return nearest
    return Math.floor(value)
  }

  const selectedStockRow = stocks.find((stock) => stock.id === effectiveStockId)
  const buyPrice = selectedStockRow ? Number(selectedStockRow.price || 0) : 0
  // 买入：预算 = 台账里给这只标的排的额度（**已被全局上限截断**）；
  // 按 ``notional × (1 + buy_rate) <= 预算`` 倒推数量。
  const buyBudget = currentPlanBudget
  const buyNotional = buyRate > -1 ? buyBudget / (1 + buyRate) : 0
  const buyAmount = buyPrice > 0 ? robustFloor(buyNotional / buyPrice) : 0
  const buyCost = buyAmount * buyPrice * (1 + buyRate)
  const buyCashAfter = Math.max(0, cash - buyCost)
  // 买完这一笔，可用现金会不会**掉到生活费红线以下**？
  const buyWarningAlarm = nextBill > 0 && buyCashAfter < nextBill

  const selectedPosition = positions.find((item) => item.stock_id === effectiveSellStockId)
  const sellAllAmount = selectedPosition ? Number(selectedPosition.sellable || 0) : 0
  const sellPrice = selectedPosition ? Number(selectedPosition.price || 0) : 0
  // 卖出：回款 = 数量 × 价 × (1 - 卖出手续费 - 印花税)（印花税单边收）。
  const sellAmount = robustFloor((sellAllAmount * sellPct) / 100)
  const sellIncome = sellAmount * sellPrice * (1 - sellRate - stampRate)
  const sellCashAfter = cash + sellIncome

  // ── P14.0：计划买入台账（多标的）──────────────────────────────────
  // 台账里排了计划的标的 —— **当前标的也在内**（它是第一个被排的）。
  // 「计划总额」是玩家**打算**花掉的钱；「还能花」是现金减去这个总额 ——
  // 两者的差就是玩家还能再往别的标的上塞多少。
  const planRows = Object.keys(planBudget)
    .filter((key) => Number(planBudget[key] || 0) > 0.005)
    .map((key) => {
      const row = stocks.find((stock) => stock.id === key)
      return {
        id: key,
        name: row ? String(row.display_name || row.name || key) : key,
        budget: Math.max(0, Number(planBudget[key] || 0)),
        current: key === effectiveStockId,
      }
    })
  const planTotal = planRows.reduce((sum, row) => sum + row.budget, 0)
  const dropPlan = (key: string) => {
    setPlanBudgetMap((prev: Record<string, number>) => {
      const next = { ...(prev || {}) }
      delete next[key]
      return next
    })
  }
  // 一局结束 / 换局 ⇒ 计划台账作废（上一局的"打算投多少"跟这一局没关系）。
  useEffect(() => {
    if (state.stage !== "playing") setPlanBudgetMap({})
  }, [state.stage])

  // ── P7.2：一键梭哈 / 一键割肉的数量 ─────────────────────────────
  const buyAllAmount = buyAmount

  // ── P13：直输框（股数 / 金额）⇒ 反推仓位比例 ──────────────────────
  //
  // 输入框显示**玩家正在敲的字**（草稿）；草稿是 ``null`` 时显示由比例
  // 算出来的值。**空串是"玩家把框清空了"**，和"没有草稿"是两回事 ——
  // 要是混为一谈，玩家一按退格，框里就立刻被派生值填回去，永远清不掉。
  // 同一侧只留一个草稿：改了这一个，另一个就让位。
  const [buySharesDraft, setBuySharesDraft] = useState<string | null>(null)
  const [buyMoneyDraft, setBuyMoneyDraft] = useState<string | null>(null)
  const [sellSharesDraft, setSellSharesDraft] = useState<string | null>(null)
  const [sellMoneyDraft, setSellMoneyDraft] = useState<string | null>(null)

  const clearTradeDrafts = () => {
    setBuySharesDraft(null)
    setBuyMoneyDraft(null)
    setSellSharesDraft(null)
    setSellMoneyDraft(null)
  }
  // 行情/现金变了 ⇒ 草稿作废（否则输入框里还留着上一次的数字）。
  const tradeSignature = [
    effectiveStockId,
    buyPrice,
    cash,
    effectiveSellStockId,
    sellPrice,
    sellAllAmount,
  ].join("|")
  useEffect(() => {
    clearTradeDrafts()
  }, [tradeSignature])

  // 买入「多少股」：这些股要花多少钱（含手续费）⇒ 除以现金 = 比例。
  const pctForBuyShares = (raw: number) => {
    const shares = Math.max(0, Math.floor(Number(raw) || 0))
    if (shares <= 0 || buyPrice <= 0 || cash <= 0) return 0
    const cost = shares * buyPrice * (1 + buyRate)
    return Math.max(0, Math.min(100, (cost / cash) * 100))
  }
  // 买入「多少钱」：先按费率**向下取整**能买多少股再折算比例 ——
  // 这样"输 100 块"就真的是 100 块上下，不会因为多出一分钱就少买一股。
  const pctForBuyMoney = (raw: number) => {
    const money = Math.max(0, Number(raw) || 0)
    if (money <= 0 || buyPrice <= 0 || cash <= 0) return 0
    const shares = Math.floor(money / (buyPrice * (1 + buyRate)))
    if (shares <= 0) return 0
    return pctForBuyShares(shares)
  }
  // 卖出「多少股」：占可卖数量的比例。
  const pctForSellShares = (raw: number) => {
    const shares = Math.max(0, Math.floor(Number(raw) || 0))
    if (shares <= 0 || sellAllAmount <= 0) return 0
    return Math.max(0, Math.min(100, (shares / sellAllAmount) * 100))
  }
  // 卖出「多少钱」：那是**期望回款**，先按费率倒推能卖多少股。
  const pctForSellMoney = (raw: number) => {
    const money = Math.max(0, Number(raw) || 0)
    if (money <= 0 || sellPrice <= 0 || sellAllAmount <= 0) return 0
    const per = sellPrice * (1 - sellRate - stampRate)
    if (per <= 0) return 0
    const shares = Math.floor(money / per)
    if (shares <= 0) return 0
    return pctForSellShares(shares)
  }

  const onBuySharesInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setBuyMoneyDraft(null)
    setBuySharesDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setPositionPct(pctForBuyShares(parsed))
  }
  const onBuyMoneyInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setBuySharesDraft(null)
    setBuyMoneyDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setPositionPct(pctForBuyMoney(parsed))
  }
  const onSellSharesInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setSellMoneyDraft(null)
    setSellSharesDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setSellPositionPct(pctForSellShares(parsed))
  }
  const onSellMoneyInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setSellSharesDraft(null)
    setSellMoneyDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setSellPositionPct(pctForSellMoney(parsed))
  }

  const buySharesShown = buySharesDraft === null ? String(buyAmount) : buySharesDraft
  const buyMoneyShown = buyMoneyDraft === null ? buyCost.toFixed(2) : buyMoneyDraft
  const sellSharesShown = sellSharesDraft === null ? String(sellAmount) : sellSharesDraft
  const sellMoneyShown = sellMoneyDraft === null ? sellIncome.toFixed(2) : sellMoneyDraft

  // ── P7：终局 Emoji 海 ───────────────────────────────────────────
  const emojiSea = useMemo(() => {
    const size = Math.max(50, Number(visual.emoji_sea_size || 60))
    const items: string[] = []
    for (let index = 0; index < size; index += 1) {
      items.push(index % 2 === 0 ? "🤡" : "🥣")
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
    h(
      "span",
      { className: "rrm-tag" },
      `${t("panel.title")} · ${stageLabel}`,
      // ── P20.0：名字 + 名号徽标（塞在标题里，**不占新的布局行**）──
      showNameBadge
        ? h(
            "span",
            {
              style: {
                marginLeft: "8px",
                padding: "1px 8px",
                borderRadius: "999px",
                border: "1px solid rgba(142, 171, 219, 0.35)",
                background: "rgba(142, 171, 219, 0.12)",
                fontSize: "12px",
                fontWeight: "700",
                color: "var(--fg)",
              },
            },
            playerName,
            nameEggLabel
              ? h(
                  "span",
                  {
                    style: {
                      marginLeft: "6px",
                      padding: "0 6px",
                      borderRadius: "999px",
                      background: "rgba(214, 72, 106, 0.16)",
                      color: "var(--danger)",
                    },
                  },
                  nameEggLabel
                )
              : null
          )
        : null
    ),
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
    // ── P23.2：【更多】—— 群友名册 / 当天新闻 / 猫娘状态 / 弹幕 ──────
    // 局中只留核心（名字、天数、钱、K 线、按钮），其余的收在这里。
    h(Button, {
      className: "rrm-guide-link",
      tone: "default",
      onClick: (event: any) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation()
        }
        setExtraOpen(true)
      },
      children: t("panel.more_info"),
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

  // ── P23.2：局中「小工具条」版的节奏控制 ──────────────────────────
  //
  // 空闲态用上面那张**完整**的节奏卡（那边有空间讲清楚）；
  // 局中只留一排小药丸（慢 / 标准 / 快 + 自动·手动 + 推进一拍），
  // 跟状态徽标、Yui 三档挤在同一行工具条里。
  const paceToolbar = h(PaceControl, {
    pace: pace,
    t: t,
    busy: busy,
    compact: true,
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

  // ── P23.1：插件**离线** ⇒ 面板整体停摆 ────────────────────────────
  //
  // 真机事故（截图 3）：插件已经停了，面板还照常渲染一堆按钮，
  // 玩家一按就弹出一串「插件未运行」的红色警告。
  //
  // 现在插件一停，面板就换成一块「插件已停止」的提示块
  // （**没有遮罩** —— 宿主禁了 fixed 定位，索性把面板整个换掉，
  // 效果一样：玩家看到的是一块说明，而不是一屏会失败的按钮），
  // 并给一个【重试连接】按钮。玩家最多看到**一条**说明。
  if (pluginOffline) {
    return (
      <Page
        className="rrm-root rrm-offline"
        title={t("panel.title")}
        subtitle={t("panel.subtitle")}
      >
        <Stack gap={12} className="rrm-offline-block">
          <Card title={t("panel.offline_title")}>
            <Stack gap={10}>
              <Text>{t("panel.offline_body")}</Text>
              <Inline gap={10} align="center" wrap>
                <Button
                  className="rrm-offline-retry"
                  onClick={() => {
                    void retryPlugin()
                  }}
                >
                  {t("panel.offline_retry")}
                </Button>
                <Text>{t("panel.offline_hint")}</Text>
              </Inline>
            </Stack>
          </Card>
        </Stack>
      </Page>
    )
  }

  // ── 局终：展示归档的结算报告 ─────────────────────────────────────
  if (state.stage === "finished" && state.settlement) {
    const report = state.settlement
    const catgirlResult = report.catgirl_result || null
    const scene = (report.death_scene || null) as DeathScene | null
    // P27.0：专属结局卡片（Wehos 的开发猫结局）。
    // 有它 ⇒ **不走**普通的破产画面三屏，直接弹专属卡片。
    const specialEnding = (report.special_ending || null) as SpecialEnding | null
    const achievements =
      (state.achievements || report.achievements || null) as Achievements | null
    // P8.1：结算页也要能「再来一局」。老版本插件没把 join 挂成 UI action 时
    // 就**不渲染**这个按钮 —— 绝不能拿一个空 action 去调。
    const joinAction = actionById("join")

    // ── P5.5 改动 4：天桥虚惊一场（分三屏，第三屏是结算数据）──────
    if (scene && sceneScreen < 3 && !specialEnding) {
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
    //   ② 破产原因 / 结局 / 入场身份之前直接显示 starved / quick 这种英文枚举。
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
          {/* ── P27.0：专属结局卡片（Wehos 的开发猫结局）─────────────
              它取代普通的破产画面 —— 没有图片资源，就用大号 🐱 顶上。── */}
          {specialEnding ? (
            <Card className="rrm-special-ending" title={String(specialEnding.title || "")}>
              <Stack gap={8}>
                {h(
                  "div",
                  { style: { fontSize: "56px", lineHeight: "1" } },
                  String(specialEnding.emoji || "🐱")
                )}
                <Text>{String(specialEnding.body || "")}</Text>
              </Stack>
            </Card>
          ) : null}
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
              {specialEnding ? null : h(BankruptArt, { alt: t("panel.settlement_image_alt") })}
              <Alert tone="info" message={t("panel.settlement_notice")} />
              {/* P18.0：**静态合规实体**（兜底）。
                  台词由 Yui 在结算时自己说；这一条是写死的声明 ——
                  万一模型漏了，合规实体也必须落地。 */}
              <Alert tone="primary" message={t("panel.settlement_risk_note")} />
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
              {/* P5.5 改动 2：身份呼应句 —— 只在破产结算页出现 */}
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

          {/* ── P7 模块二 ④：Emoji 海结算（至少 50 个 🤡 / 🥣）── */}
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

  // ── P23.0：【耻辱柱】弹窗（历史破产记录）───────────────────────────
  //
  // 表格用宿主**原生** ``DataTable``（自己画表格 = 自己写危险 CSS）。
  // 行已经由后端按**存活天数升序**排好了（越短越丢人）。
  const shameModal = h(Modal, {
    open: shameOpen,
    title: t("panel.shame_title"),
    size: "lg",
    closeOnBackdrop: true,
    closeOnEscape: true,
    onClose: () => {
      setShameOpen(false)
    },
    children: h(DataTable, {
      data: shameRows,
      emptyText: t("panel.shame_empty"),
      maxRows: 50,
      columns: [
        {
          key: "name",
          label: t("panel.shame_col_name"),
          render: (row: any) =>
            h("span", { className: "rrm-shame-name" }, String(row.name || "")),
        },
        {
          key: "day",
          label: t("panel.shame_col_day"),
          render: (row: any) =>
            h("span", { className: "rrm-shame-day" }, `${Number(row.day || 0)}`),
        },
        {
          key: "reason",
          label: t("panel.shame_col_reason"),
          render: (row: any) =>
            h("span", { className: "rrm-shame-reason" }, String(row.reason || "")),
        },
      ],
    }),
  })

  // ── 没开局 ───────────────────────────────────────────────────────
  if (state.stage !== "playing") {
    const joinAction = actionById("join")
    const joinActionId = joinAction
      ? String(joinAction.entry_id || joinAction.id || "join")
      : ""
    /** 按档位开局：走 P11.1 的全局请求锁（狂点只会被静默丢弃，不会打爆宿主）。 */
    const joinMode = (mode: string) => {
      if (!joinActionId) return
      // P20.0：把玩家自报名号一起带过去（后端会再洗一遍）。
      void callAction(joinActionId, { mode, player_name: nameDraft.trim() })
    }
    /**
     * P14.0：一块**档位卡**。
     *
     * 身份色写在顶部的渐变色带上，文字压在浅底（无尽那张是深底）上 ——
     * 颜色分得开，字也还读得清。
     */
    const modeCard = (opts: {
      title: string
      tagline: string
      hint: string
      band: string
      bg: string
      titleColor: string
      tagColor: string
      hintColor: string
      onClick: () => void
    }) =>
      h(
        "button",
        {
          type: "button",
          className: "rrm-mode-card",
          style: {
            display: "block",
            width: "100%",
            textAlign: "left",
            padding: "0",
            border: "1px solid rgba(142, 171, 219, 0.26)",
            borderRadius: "12px",
            overflow: "hidden",
            background: opts.bg,
            cursor: "pointer",
            fontFamily: "inherit",
          },
          onClick: opts.onClick,
        },
        h("div", { style: { height: "10px", background: opts.band } }),
        h(
          "div",
          {
            style: {
              padding: "12px 14px",
              display: "flex",
              flexDirection: "column",
              gap: "3px",
            },
          },
          h(
            "span",
            {
              style: {
                fontSize: "17px",
                fontWeight: "800",
                letterSpacing: "0.5px",
                color: opts.titleColor,
              },
            },
            opts.title
          ),
          h(
            "span",
            { style: { fontSize: "12px", fontWeight: "700", color: opts.tagColor } },
            opts.tagline
          ),
          h("span", { style: { fontSize: "12px", color: opts.hintColor } }, opts.hint)
        )
      )
    // ── P14.0：【命运挑战】的二级档位（入门 / 进阶 / 地狱）────────────
    const challengeLevels = [
      {
        mode: "classic_beginner",
        title: t("panel.mode_level_beginner"),
        tag: t("panel.mode_level_beginner_tag"),
        hint: t("panel.mode_level_beginner_hint"),
        accent: "#0f6b4c",
        band: "linear-gradient(90deg, #8fe0bf 0%, #17916f 100%)",
        bg: "linear-gradient(180deg, #effaf4 0%, #ddf3e7 100%)",
      },
      {
        mode: "classic_advanced",
        title: t("panel.mode_level_advanced"),
        tag: t("panel.mode_level_advanced_tag"),
        hint: t("panel.mode_level_advanced_hint"),
        accent: "#9c2b45",
        band: "linear-gradient(90deg, #f5c451 0%, #d6486a 100%)",
        bg: "linear-gradient(180deg, #fff6e2 0%, #ffe3e6 100%)",
      },
      {
        mode: "classic_hell",
        title: t("panel.mode_level_hell"),
        tag: t("panel.mode_level_hell_tag"),
        hint: t("panel.mode_level_hell_hint"),
        accent: "#e7e0ff",
        band: "linear-gradient(90deg, #7a3a63 0%, #d6486a 100%)",
        bg: "linear-gradient(180deg, #2b2650 0%, #16122f 100%)",
      },
    ]
    const challengeModal = h(Modal, {
      open: challengeOpen,
      title: t("panel.mode_challenge_modal_title"),
      closeOnBackdrop: true,
      closeOnEscape: true,
      size: "md",
      children: h(
        Stack,
        { gap: 10 },
        h(Text, null, t("panel.mode_challenge_modal_hint")),
        challengeLevels.map((level) =>
          h(
            "button",
            {
              type: "button",
              className: "rrm-mode-card",
              style: {
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "0",
                border: "1px solid rgba(142, 171, 219, 0.26)",
                borderRadius: "12px",
                overflow: "hidden",
                background: level.bg,
                cursor: "pointer",
                fontFamily: "inherit",
              },
              onClick: () => {
                setChallengeOpen(false)
                joinMode(level.mode)
              },
            },
            h("div", { style: { height: "8px", background: level.band } }),
            h(
              "div",
              {
                style: {
                  padding: "10px 12px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "2px",
                },
              },
              h(
                "span",
                {
                  style: {
                    fontSize: "15px",
                    fontWeight: "800",
                    color: level.accent,
                  },
                },
                level.title
              ),
              h(
                "span",
                { style: { fontSize: "12px", fontWeight: "700", color: level.accent } },
                level.tag
              ),
              h(
                "span",
                { style: { fontSize: "12px", color: level.accent } },
                level.hint
              )
            )
          )
        )
      ),
    })
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
              {/* ── P20.0：名字输入框（在档位卡**上方**）──────────────
                  玩家先报个名，再挑档位。

                  提示语写得欠一点是故意的（"别用真名，丢人"）——
                  这是直播向的沙盘，认真取名反而出戏。

                  防呆在前端先做一遍（去换行、限长 12）；**后端还会再洗一遍**
                  —— 面板永远不可信。 */}
              {h(
                Field,
                {
                  label: t("panel.name_input_label"),
                  // P21.0：两行提示 —— 一行劝退，一行**吊胃口**
                  // （「名字太嚣张，容易触发特殊彩蛋」）。
                  help: h(
                    "span",
                    null,
                    t("panel.name_input_hint"),
                    h("br", null),
                    t("panel.name_input_hint2")
                  ),
                },
                h(Input, {
                  value: nameDraft,
                  placeholder: t("panel.name_input_placeholder"),
                  onChange: onNameInput,
                })
              )}
              {/* ── P7.4：真正的开局入口 ──────────────────────────────
                  0.8.3 事故：空闲态只有一行冰冷的文字，玩家**没法开局** ——
                  要么去聊天框打「加入一局」，要么就卡在这儿。这里给两个大按钮，
                  点一下直接开一局。

                  注意 ``joinAction`` 可能不存在（老版本插件没有把 join 挂成
                  UI action）—— 那就不渲染按钮，绝不能拿一个空 action 去调，
                  否则宿主会把它当成面板运行时错误上报。 */}
              {/* ── P14.0：档位卡（街机 / 命运挑战 / 无尽）──────────────
                  三档必须**一眼分得开**：
                  街机 = 纯爽局（浅绿，「老爸给的零花钱」）；
                  命运挑战 = 要动真格的（黄→深红，断供 / 杠杆 / 生存压力），
                  点开是**二级选择**（入门 / 进阶 / 地狱）；
                  无尽 = 深渊（深蓝紫 + 血红，「最后的筹码」）。

                  颜色全部走**内联渐变**（P14 的纪律：极度克制地使用 CSS），
                  一行新 CSS 都不加。 */}
              {joinAction ? (
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      flexDirection: "column",
                      gap: "10px",
                    },
                  },
                  modeCard({
                    title: t("panel.mode_arcade_title"),
                    tagline: t("panel.mode_arcade_tag"),
                    hint: t("panel.mode_arcade_hint"),
                    band: "linear-gradient(90deg, #8fe0bf 0%, #17916f 100%)",
                    bg: "linear-gradient(180deg, #effaf4 0%, #ddf3e7 100%)",
                    titleColor: "#0f6b4c",
                    tagColor: "#17916f",
                    hintColor: "#5d7d70",
                    onClick: () => joinMode("classic_arcade"),
                  }),
                  modeCard({
                    title: t("panel.mode_challenge_title"),
                    tagline: t("panel.mode_challenge_tag"),
                    hint: t("panel.mode_challenge_hint"),
                    band: "linear-gradient(90deg, #f5c451 0%, #d6486a 100%)",
                    bg: "linear-gradient(180deg, #fff6e2 0%, #ffe3e6 100%)",
                    titleColor: "#9c2b45",
                    tagColor: "#c0455f",
                    hintColor: "#8a6a70",
                    onClick: () => setChallengeOpen(true),
                  }),
                  modeCard({
                    title: t("panel.mode_infinite_title"),
                    tagline: t("panel.mode_infinite_tag"),
                    hint: t("panel.mode_infinite_hint"),
                    band: "linear-gradient(90deg, #7a3a63 0%, #d6486a 100%)",
                    bg: "linear-gradient(180deg, #2b2650 0%, #16122f 100%)",
                    titleColor: "#e7e0ff",
                    tagColor: "#b9a8f0",
                    hintColor: "#8d86b8",
                    onClick: () => joinMode("infinite"),
                  })
                )
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
              {/* ── P23.0：【耻辱柱】──────────────────────────────────
                  历史破产记录（名字 / 存活天数 / 破产原因），
                  **按存活天数升序** —— 越短越丢人，排在最前面。
                  点开是宿主原生的 DataTable（不自己画表格 = 不写危险 CSS）。 */}
              <Button
                className="rrm-shame-btn"
                onClick={() => {
                  void openShame()
                }}
              >
                {t("panel.shame_button")}
              </Button>
              {/* ── P23.1：Yui 的**操控模式**（空闲态 / 局中都有）─────────
                  三档（玩家拍板）：
                  · live     直播托管 —— Yui 一站式负责买卖；
                  · assist   辅助式（默认）—— 只给建议，玩家明示后才可以动手；
                  · spectate 纯看戏 —— 只评论，绝不碰账户。

                  **live 只能从这里开** —— Yui 自己不许给自己升权。
                  P23.1：局中也能切（见下面局中页的同一份选择器）——
                  以前只有空闲态能切，主播「开面板就进游戏、进了游戏就切不了」。 */}
              <YuiModePicker
                mode={yuiMode}
                t={t}
                onPick={(mode: string) => {
                  void callAction("yui_mode", { mode })
                }}
              />
              {/* ── P24.0：【话痨级别】—— 就在 Yui 模式旁边 ──────────────
                  三档（安静 / 正常 / 话痨）直接决定后台**每隔几个 tick**
                  开一次她的回合。局中页也有一份（下面工具条里）。 */}
              <YuiTalkPicker
                level={yuiTalkLevel}
                t={t}
                onPick={(level: string) => {
                  void callAction("yui_talk_level", { level })
                }}
              />
            </Stack>
          </Card>
          {paceCard}
          {challengeModal}
          {guideModal}
          {shameModal}
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
  //   ② **永远**给一个「我不借了，把我清出去吧」的逃生按钮；
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
    /* ── P23.2：局中页 = 「直播看板」───────────────────────────────
       * 页面**锁死一屏**（``height:100vh; overflow:hidden``），
       * 内容左右分栏（宿主 ``Split``，``ratio="3fr 2fr"`` = 60/40），
       * 页面本身**绝不出现纵向滚动条** —— 只有行情 / 持仓两张卡内部能滚。
       *
       * Page **不传标题**：品牌名在标题行里已经有了，而标题块会白占
       * 一行（≈40px）。局中每一像素都要留给核心数据。 */
    <Page className={`${rootClass} rrm-live-page${arcadeSimple ? " rrm-arcade-simple" : ""}`}>
      <Stack className={`${stageClass} rrm-live-stage`} gap={8}>
        {h("style", null, RRM_CSS)}
        {/* ── P18.0：动态聚焦引导（开场 / 上课 / 结课）───────────────── */}
        {h(TutorialOverlay, {
          tutorial: tutorial,
          t: t,
          onBegin: () => tutorialCall("tutorial_begin"),
          onAdvance: () => tutorialCall("tutorial_advance"),
          onSkip: () => tutorialCall("tutorial_skip"),
        })}
        {/* ── 0.8.1 病危表现：**只有**一条红色 Alert 警告条（文档流内）。
            绝不用闪烁 / 半透明 / 遮罩 —— 那是 0.8.0 崩 UI 的根因。───── */}
        {critical ? (
          <Alert
            tone="danger"
            message={`${visual.critical_emoji || "💸"} ${t("visual.critical")}`}
          />
        ) : null}
        {titleBar}
        {/* ── P23.0：Yui 介入横幅（**专属于猫娘**，区别于玩家自己的操作）──
            紫色调 + 爪印 + 呼吸高光 —— 面板上只有这一处是这个颜色，
            一眼就知道「这笔不是玩家点的」。 */}
        {yuiBanner ? (
          <Card
            className={
              yuiBannerHot ? "rrm-yui-banner rrm-yui-banner-hot" : "rrm-yui-banner"
            }
          >
            <Stack gap={6}>
              <Inline gap={10} align="center">
                <span className="rrm-yui-banner-paw">🐾</span>
                <span className="rrm-yui-banner-title">
                  {t("panel.yui_banner_title")}
                </span>
              </Inline>
              <span className="rrm-yui-banner-body">
                {t("panel.yui_banner_body", {
                  action:
                    yuiBanner.action === "buy"
                      ? t("panel.yui_action_buy")
                      : t("panel.yui_action_sell"),
                  stock: String(yuiBanner.stock_name || yuiBanner.stock_id || ""),
                  amount: Number(yuiBanner.amount || 0).toFixed(4),
                })}
              </span>
            </Stack>
          </Card>
        ) : null}
        {directorConsole}
        {boomModal}
        {guideModal}
        {meowModal}
        {storyModal}

        {/* ── P7.2 ⓪：状态条 = **局中的那一行工具条** ──────────────
            P23.2：状态徽标 + **Yui 三档徽标** + **节奏控制** 全部压进
            这一行 —— 以前它们是三块（状态条一张、节奏卡一张、
            Yui 模式一摞按钮），光这三块就吃掉半屏。
            现在：状态 → Yui → 节奏，一行看完。 */}
        <Inline className="rrm-anchor-status-bar rrm-live-toolbar" gap={8} wrap align="center">
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
          {/* ── P21.0：家道徽标（丰盈 / 断供 / 濒临破产）──────────────
              街机档没有账单 ⇒ 家道**恒定**是「丰盈」⇒ 纯噪音，藏起来。 */}
          {showFamilyBadge ? <StatusBadge tone="default" label={stageLabel} /> : null}
          {/* ── P21.0：「距离强制平仓线」—— 街机档没有线可距 ⇒ 不显示。
              （这行字在街机档永远是「—」，而且「强制平仓」四个字本身就是
                新手最不需要看到的词。） */}
          {showKillLine ? (
            <Text>
              {t("panel.distance_to_kill")}:{" "}
              {state.distance_to_kill === null || state.distance_to_kill === undefined
                ? "—"
                : money(state.distance_to_kill)}
            </Text>
          ) : null}
          {/* ── P23.1：Yui 的**操控模式**（局中也能切）──────────────────
              P23.2：压成**一小排徽标**，就坐在状态条旁边。

              真机死锁：「为了切换直播托管，必须先打开面板；打开面板就进了游戏；
              进了游戏就不能切换模式了。」—— 现在局中就有同一份选择器。

              切模式只是改一个设置项（``_yui_apply_mode``），
              **不碰结算状态、不碰引擎** ⇒ 局中切换没有任何冲突。
              **live 只能从这里开** —— Yui 自己不许给自己升权。 */}
          <YuiModePicker
            mode={yuiMode}
            t={t}
            compact
            onPick={(mode: string) => {
              void callAction("yui_mode", { mode })
            }}
          />
          {/* ── P24.0：【话痨级别】—— 紧挨着 Yui 模式，同一行小药丸 ────
              直播时没动静太干了：这三档直接决定后台每隔几个 tick 开一次
              她的回合（安静 = 只在出大事时开口）。

              同样是「设一个设置项 + 落库」，**不碰结算状态、不碰引擎**
              ⇒ 局中随时可调，不会有冲突。 */}
          <YuiTalkPicker
            level={yuiTalkLevel}
            t={t}
            compact
            onPick={(level: string) => {
              void callAction("yui_talk_level", { level })
            }}
          />
          {/* ── P23.2：节奏控制也压进这一行（慢 / 标准 / 快 + 自动·手动
              + 推进一拍）—— 以前它自己占一整张卡。 */}
          {paceToolbar}
        </Inline>

        {/* ── P19.0：导演段的「进度」（**只在开关打开时才渲染**）────────
            直播特制版的开关 ``[director] show_progress = true``。
            开关关闭时 ``state.director_progress`` 是 undefined ⇒ **整块不渲染**，
            面板与以前逐像素一样。 */}
        {state.director_progress ? (
          <Inline gap={8} wrap align="center" className="rrm-director-progress">
            <StatusBadge
              tone={(state.director_progress.hype || 0) > 0.6 ? "danger" : "default"}
              label={t("director.progress.hype", {
                value: (state.director_progress.hype || 0).toFixed(2),
              })}
            />
            <StatusBadge
              tone="default"
              label={t("director.progress.chapter", {
                current: state.director_progress.chapter || 0,
                total: state.director_progress.chapter_total || 0,
              })}
            />
            {state.director_progress.chapter_title ? (
              <StatusBadge
                tone="default"
                label={state.director_progress.chapter_title}
              />
            ) : null}
          </Inline>
        ) : null}

        {/* ── P23.2：节奏卡与 Yui 模式**已经搬进上面那行工具条** ─────
            （自动 / 手动依然一眼可见，但不再各占一块地方。） */}

        {/* ══ P27.1：顶部概览（总资产 + 赚亏 + 现金红线）══════════════
            参照金融小游戏的顶栏：左半格是**大字总资产**，右半格是
            两张小卡（赚亏 / 现金红线）。
            这三样都是「一眼要看」的 ⇒ 顶格放在工具条下面。

            右半格那两格用宿主 ``Grid``（等宽两列）——
            宿主 Grid 只能等宽，但这里要的正好是两格等宽。── */}
        <Grid cols={2} gap={8} className="rrm-anchor-overview rrm-live-overview">
          <Card title={t("panel.overview_assets_title")}>
            <Stack gap={6}>
              <BigStat
                label={assetsLabel}
                value={money(totalAssets)}
                hint={t("visual.assets_hint", { stage: stageLabel })}
                color={flashColor}
                size="clamp(34px, 4.4vw, 72px)"
              />
              {/* 与 Yui 的对比：领先 / 落后都直说，不粉饰。 */}
              <Text>{yuiCompareText}</Text>
            </Stack>
          </Card>
          <Grid cols={2} gap={8}>
            <Card title={t("visual.pnl_title")}>
              <BigStat
                label={t("visual.total_pnl")}
                value={`${pnlShown > 0 ? "+" : ""}${money(pnlShown)}`}
                hint={pnlHint}
                color={pnlColor}
                size="clamp(24px, 2.8vw, 44px)"
                pop={pnlPop}
                // P23.0：老虎机闪光（赚绿 / 亏红）。
                glow={pnlFlash}
              />
            </Card>
            <Card title={t("panel.overview_cash_title")}>
              <Stack gap={6}>
                {/* P14.0：资金水位条 —— 街机档默认藏起来，
                    但教程圈到它的时候强制显示（锚点还在它身上）。 */}
                {showGauge
                  ? h(CashGauge, {
                      cash: cash,
                      bill: nextBill,
                      crisis: cashCrisis,
                      alarmOn: gaugeAlarm,
                      t: t,
                      // 前两关压成**一行字**（完整的水位条在那里是干扰）。
                      compact: simpleUi,
                    })
                  : null}
                <BigStat
                  label={cashLabel}
                  value={money(state.cash)}
                  hint={stageHint}
                  // P14.0：现金跌破生活费红线 ⇒ 数字**变红并轻微闪烁**。
                  color={
                    cashCrisis
                      ? gaugeAlarm
                        ? "rgba(214, 72, 106, 0.45)"
                        : "var(--danger)"
                      : undefined
                  }
                  size="clamp(20px, 2.2vw, 34px)"
                  // P23.0：濒危发病 ① —— 资金数字**随机抖动**。
                  shake={cashShake}
                />
              </Stack>
            </Card>
          </Grid>
        </Grid>

        {/* ══ P23.2：左右分栏（宿主 ``Split``，``ratio="3fr 2fr"`` = 60/40）══
             P27.1 之后的分工：
             左列 = K 线主视觉（含资产曲线）+ 行情方块；
             右列 = 连击 / 账单 + 持仓方块 + 买卖比例 + 每日动作。
             三大数字搬去了**顶部概览**，买卖按钮搬去了**底部操作台**
             （整条压在下沿，三键并排）。
             宿主 ``Grid`` / ``Columns`` 只能等宽，做不到 60/40；
             ``Split`` 把 ``ratio`` 原样写进 ``--split-template`` ⇒ 原生比例。 */}
        <Split className="rrm-live-board" ratio="3fr 2fr" gap={8}>
          {/* ── 左列（60%）────────────────────────────────────────────── */}
          <Stack className="rrm-live-col" gap={8}>
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
                    onChange={(value: any) => setKlineStockPick(String(value))}
                  />
                  <ActionButton
                    action={actionById("kline")}
                    label={t("panel.kline_fetch")}
                    values={{ stock_id: klineStockId }}
                    onResult={(result: any) => {
                      // P23.1：存进**按标的分的表**（A → B → A 切回来不用重拉）。
                      if (result && result.stock_id) {
                        const key = String(result.stock_id)
                        setKlineDetailMap((prev: Record<string, any>) => ({
                          ...prev,
                          [key]: result,
                        }))
                      }
                    }}
                    onError={onActionError}
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
              </>
            )}
            {/* P23.1：K 线区**一直**在那儿 —— 哪怕一个点都没有，
                也得占满 54vh，绝不能塌成一行字。 */}
            <Kline
              points={klinePoints}
              emptyText={t("panel.kline_empty")}
              warmingText={t("panel.kline_warming")}
            />
            {/* ── P27.1：你 vs Yui 的资产曲线**压在 K 线正下方** ────────
                两张图共用一块深色画布 ⇒ 视觉上是一整块「行情区」，
                颜色对比也最狠（蓝线 vs 粉线，深底上谁也压不过谁）。
                图例 + 那句挑衅的话紧跟在曲线下面。── */}
            {h(
              "div",
              { className: "rrm-equity-inline" },
              h(
                "div",
                { className: "rrm-equity-canvas" },
                equityPlayer.length > 1
                  ? h(EquityBattle, {
                      player: equityPlayer,
                      yui: equityYui,
                      title: t("panel.equity_title"),
                      width: 320,
                      height: 56,
                    })
                  : h(Text, null, t("panel.equity_empty"))
              ),
              h(
                "div",
                { className: "rrm-equity-legend" },
                h(
                  "span",
                  {
                    style: {
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                      fontSize: "12px",
                    },
                  },
                  h("span", {
                    style: {
                      width: "10px",
                      height: "3px",
                      borderRadius: "2px",
                      background: EQUITY_PLAYER_COLOR,
                      display: "inline-block",
                    },
                  }),
                  t("panel.equity_player")
                ),
                h(
                  "span",
                  {
                    style: {
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                      fontSize: "12px",
                    },
                  },
                  h("span", {
                    style: {
                      width: "10px",
                      height: "3px",
                      borderRadius: "2px",
                      background: EQUITY_YUI_COLOR,
                      display: "inline-block",
                    },
                  }),
                  t("panel.equity_yui")
                ),
                h("span", { className: "rrm-equity-taunt" }, t("panel.equity_taunt"))
              )
            )}
          </Stack>
        </Card>

        {/* ── P23.2：三大数字 / 水位条 / 连击 / 账单 **都搬到右列**了 ──
            （数字要紧挨着买卖按钮 —— 资金变化和手部动作同屏，
            主播一边看数字一边下单，不用来回扫屏幕。）── */}

        {/* ── P8.0 ③：中半区 —— 行情**方块卡片网格** ──────────────────
            用宿主 `Columns` 的 fluid 模式：面板宽就是 4 列，窄就是 2 列。
            每张方块卡 = 股票名（+ 群友头像）+ 微型折线 + 巨大涨跌百分比。
            卡片是 `Columns` 的**直接**子元素 —— 中间没有任何 div 包裹层。

            P23.2：整张卡**吃满左列剩下的高度**，方块在里面**纵向滚动**
            （宿主原生 ``ScrollArea``）—— 页面本身一条滚动条都不出。
            P23.2 排序：**妖股在前**（爆炸标签 → 波动率大 → 当天振幅大），
            稳定 / 中性的沉到后排。观众第一眼落在最有戏的那几只上。 */}
        <Card
          className="rrm-anchor-market-grid rrm-live-flex"
          title={t("visual.market_title")}
        >
          {stocks.length === 0 ? (
            <Text>{t("panel.no_stocks")}</Text>
          ) : (
            <ScrollArea axis="y" className="rrm-live-scroll">
              <Columns fluid minWidth={228} gap={8}>
                {visibleStocks.map((stock) => h(StockTile, { key: stock.id, stock, t }))}
              </Columns>
              {hiddenStockCount > 0 ? (
                <Text>{t("panel.more_stocks", { count: hiddenStockCount })}</Text>
              ) : null}
            </ScrollArea>
          )}
        </Card>

        {/* ── P27.1：持仓列表**搬去右列**了 ──────────────────────────
            左列现在只管「看」（K 线 + 行情），右列管「动手前的一切」
            （持仓 / 比例 / 每日动作）⇒ 两边的职责不打架。── */}

            {/* ── P27.1：左列到此为止（K 线 + 行情）────────────────── */}
          </Stack>

          {/* ── 右列（40%）：**持仓 → 比例 → 每日动作** ────────────────
              P27.1：三大数字与水位条搬去了**顶部概览**，买卖按钮搬去了
              **底部操作台** —— 右列现在只留「动手前要看的东西」。── */}
          <Stack className="rrm-live-col" gap={8}>
            {/* ── P27.1：三大数字（赚亏 / 现钱 / 身家）**搬去顶部概览**了 ──
                顶部那三格是「一眼要看」的东西；
                右列现在只留「钱相关的操作件」与「持仓」。── */}

            {/* ── P16.0：连击徽章（P21.0：只有后面三档才给看）────────── */}
            {showCombo ? h(ComboBadge, { streak: comboStreak, best: comboBest, t: t }) : null}

            {/* ── P27.1：资金水位条**搬去顶部概览**了（现金红线那张卡）──
                街机档默认藏起来；教程圈到它的时候强制显示。── */}

            {/* ── P7.2 ③：明日账单（**只留交不起的那版**）──────────────
                P21.0：街机档没有账单 ⇒ 整块不渲染（恒定的「明天要交 0」是噪音）。
                P23.2：「钱够了」那版也**删掉** —— 水位条已经说了同一件事，
                两行字讲两遍同一件事就是噪音。 */}
            {showBillAlert && (state.bill_gap || 0) > 0 ? (
              <Alert
                tone="danger"
                message={t("panel.bill_warn", {
                  gap: money(state.bill_gap),
                  next: money(state.next_bill),
                  cash: money(state.cash),
                  missed: state.missed_bills || 0,
                })}
              />
            ) : null}

            {/* ── P27.1：持仓列表（只留名称 / 市值 / 大号盈亏）─────────
                从左列搬到了右列：右列是「动手之前看的」，
                持仓正好属于这一类。高度仍用 vh 表达（不写死 px）。

                P28.0：街机档**不显示**这一块（多标的持仓明细在街机档
                是纯噪音）—— 街机档只留 K 线 / 两键 / 现钱。── */}
            {arcadeSimple ? null : (
            <Card className="rrm-anchor-position-grid" title={t("visual.holdings_title")}>
              {positions.length === 0 ? (
                <Text>{t("panel.no_positions")}</Text>
              ) : (
                <ScrollArea
                  axis="y"
                  height="clamp(84px, 11vh, 170px)"
                  className="rrm-live-scroll"
                >
                  <Columns fluid minWidth={196} gap={8}>
                    {shownPositions.map((position) =>
                      h(PositionTile, { key: position.stock_id, position, t })
                    )}
                  </Columns>
                  {hiddenPositionCount > 0 ? (
                    <Text>{t("panel.positions_more", { count: hiddenPositionCount })}</Text>
                  ) : null}
                </ScrollArea>
              )}
              {/* ── P14.0：集中度警告（P21.0：只有后面三档才给看）────────
                  前两关的诉求是「闭着眼睛瞎买也能赚钱」——
                  「你的钱全压在一只上」这种警告在这里是**干扰**。 */}
              {showConcentration && (state.concentration || 0) > 0.6 ? (
                <Alert
                  tone="warning"
                  message={t("visual.concentration_warn", {
                    value: percent(state.concentration),
                  })}
                />
              ) : null}
            </Card>
            )}

        {/* ── P13：操作台 · 连续仓位 + 直输框 + 执行 ───────────────
            P12 把「一键梭哈 / 一键割肉」换成了仓位滑块，但滑块只有
            25/50/75/100 四档 —— 仓位还是被**量化**的。P13 把它拆干净：

            * 滑块是宿主**原生** ``Slider``，步进改成 **1%**（连续）；
            * **删掉** 25/50/75/100 那四档快捷键（那才是"去量子化"的
              拦路虎）；
            * 预览区多两个**直输框**（股数 / 金额），输入后系统**反推**
              仓位比例并同步滑块；
            * 预览用**引擎那一套费率**（``state.fees``），所以"预计花费"
              和真扣的钱**一致**；
            * 执行按钮走 ``callAction`` ⇒ **纳入 P11.1 的全局请求锁**
              （``ActionButton`` 不支持 ``disabled``，也进不了这把锁）。 */}
        {/* ── P23.2：仓位卡（右列）—— **只管仓位**（滑块 / 预览 / 直输框）──
            P23.2：买卖按钮搬到下面那张「下单卡」——
            右列必须能**一眼看清滑块、预览数字、买卖按钮**这三样，
            巨号按钮和精调件挤在一张卡里只会两败俱伤。 */}
        <Card
          className="rrm-trade-finetune-card rrm-live-flex"
          title={t("panel.trade_finetune_title")}
        >
          {/* ── P23.0：操作区的「精简 / 展开」开关 ─────────────────────
              直播时主播可以把整块精调件收起来 —— 屏幕上只留买卖按钮，
              干净、爽、不会被一堆滑块和预览框分散注意力。 */}
          <Button
            className="rrm-trade-finetune"
            onClick={() => {
              setTradeFineTuneOpen(!tradeFineTuneOpen)
            }}
          >
            {tradeFineTuneOpen
              ? t("panel.trade_finetune_close")
              : t("panel.trade_finetune_open")}
          </Button>
          {/* P23.2：精调区自己会滚（宿主原生 ``ScrollArea``）——
              展开时再多的滑块也不会把整页撑出滚动条。 */}
          <ScrollArea axis="y" className="rrm-live-scroll">
            {/* ── P14.0：买 / 卖**两条**独立仓位 ───────────────────────
                P13 只有一个"仓位"，买和卖共用它 —— 拖一下就同时改了
                "买多少"和"卖多少"，本来就该分开。P14 拆成两条，
                左右各看各的，标的也各自带在顶上。

                买入那条还多一层**全局上限**：台账里别的标的占掉的额度
                会把它**截断**（后拉动的滑条只能拉到上限为止）——
                那股"拉扯"的阻力感，就是在告诉玩家他的极限在哪。 */}
            {tradeFineTuneOpen ? (
              <>
            <Grid cols={2} gap={12} className="rrm-trade-sides">
              {/* ── 买入这一侧 ─────────────────────────────────── */}
              <Stack gap={6} className="rrm-trade-side">
                <Inline gap={10} wrap={false} align="center" className="rrm-select-row">
                  <Select
                    value={effectiveStockId}
                    options={stockOptions}
                    onChange={(value: any) => setSelectedStock(String(value))}
                  />
                </Inline>
                {/* P27.1：「把兜里的钱全压在这只上」这类**说明文字**
                    搬去了【更多】弹窗 —— 局中屏幕上只留数字和控件。 */}
                <Inline gap={8} align="center" justify="space-between">
                  <Text>{t("panel.trade_buy_side")}</Text>
                  <Text>{t("panel.trade_pct", { pct: Math.round(buyPositionPct) })}</Text>
                </Inline>
                {h(
                  "div",
                  {
                    className: "rrm-trade-slider",
                    // 当前比例写进 CSS 变量 ⇒ 轨道渐变能画出"已填充到哪"。
                    // 轨道满格 = 本轮**上限**（不是 100%）—— 上限被占满时
                    // 整条填满，玩家一眼看见自己已经顶到天花板。
                    style: { "--rrm-trade-fill": `${Math.round(buyFillPct * 10) / 10}%` },
                  },
                  h(Slider, {
                    min: 0,
                    // **上限不是 100**：别的标的占掉的额度会把这条滑条截断。
                    max: Math.max(1, Math.floor(capPct)),
                    step: 1,
                    value: buyPositionPct,
                    showValue: false,
                    // 上限被吃光了 ⇒ 这条滑条**锁死**（连 1% 都分不到）。
                    disabled: busy || capPct <= 0,
                    onChange: (value: any) => {
                      setPositionPct(Number(value))
                      clearTradeDrafts()
                    },
                  })
                )}
                <Text>{t("panel.trade_cap_hint", {
                    cap: Math.max(0, Math.floor(capPct)),
                    used: othersBudget.toFixed(2),
                  })}
                </Text>
              </Stack>

              {/* ── 卖出这一侧 ─────────────────────────────────── */}
              <Stack gap={6} className="rrm-trade-side">
                <Inline gap={10} wrap={false} align="center" className="rrm-select-row">
                  <Select
                    value={effectiveSellStockId}
                    options={positionOptions}
                    onChange={(value: any) => setSellStock(String(value))}
                  />
                </Inline>
                {/* P27.1：说明文字同样搬去了【更多】弹窗。 */}
                <Inline gap={8} align="center" justify="space-between">
                  <Text>{t("panel.trade_sell_side")}</Text>
                  <Text>{t("panel.trade_pct", { pct: Math.round(sellPct) })}</Text>
                </Inline>
                {h(
                  "div",
                  {
                    className: "rrm-trade-slider",
                    style: { "--rrm-trade-fill": `${Math.round(sellPct * 10) / 10}%` },
                  },
                  h(Slider, {
                    min: 0,
                    max: 100,
                    step: 1,
                    value: sellPct,
                    showValue: false,
                    onChange: (value: any) => {
                      setSellPositionPct(Number(value))
                      clearTradeDrafts()
                      // P21.0：第一次动卖出滑块 ⇒ 飘出新手兜底提示。
                      triggerGhostHint()
                    },
                  })
                )}
                <Text>{t("panel.trade_sell_hint", { amount: sellAllAmount })}</Text>
              </Stack>
            </Grid>

            {/* ── P14.0：买入后的**水位预警** ─────────────────────────
                玩家拉满滑块的时候，得让他看见"买完还剩多少"，
                以及"这一笔会不会把生活费吃掉"—— 低于红线就变红。 */}
            {h(
              "div",
              {
                className: "rrm-trade-warning",
                style: {
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "8px",
                  flexWrap: "wrap",
                  padding: "8px 10px",
                  borderRadius: "8px",
                  fontSize: "12px",
                  lineHeight: "1.5",
                  background: buyWarningAlarm
                    ? "rgba(214, 72, 106, 0.12)"
                    : "rgba(91, 143, 249, 0.08)",
                  border: buyWarningAlarm
                    ? "1px solid rgba(214, 72, 106, 0.5)"
                    : "1px solid rgba(91, 143, 249, 0.22)",
                  color: buyWarningAlarm ? "var(--danger)" : "var(--muted)",
                },
              },
              h(
                "span",
                null,
                t("panel.trade_after_buy", { cash: buyCashAfter.toFixed(2) })
              ),
              buyWarningAlarm
                ? h(
                    "span",
                    { style: { fontWeight: "700" } },
                    t("panel.trade_below_bill", { bill: nextBill.toFixed(2) })
                  )
                : null
            )}

            {/* ── P21.0：新手兜底的「幽灵提示」──────────────────────
                跳过教程直接开干的人，第一次动手时旁边飘出这一行。
                静态 UI 文案（**不是** Yui 台词）—— 只负责指路。 */}
            {ghostHintOn
              ? h(
                  "div",
                  {
                    className: ghostHintIn
                      ? "rrm-ghost-hint rrm-ghost-hint-in"
                      : "rrm-ghost-hint",
                    style: {
                      fontSize: "12px",
                      lineHeight: "1.6",
                      color: "var(--muted)",
                      fontStyle: "italic",
                      padding: "2px 2px 0",
                    },
                  },
                  t("panel.ghost_hint")
                )
              : null}
              </>
            ) : (
              /* ── P23.0：折叠态 —— **一行摘要**（买多少 / 卖多少 / 买哪只）──
                  折叠不是"藏起来让人猜"：这一行把两个巨号按钮会干的事
                  写得明明白白，想改再点上面那个开关。 */
              h(
                "div",
                { className: "rrm-trade-summary" },
                h(
                  "span",
                  null,
                  t("panel.trade_summary", {
                    buy: `${Math.round(buyPositionPct)}%`,
                    sell: `${Math.round(sellPct)}%`,
                    stock: String(
                      (selectedStockRow && (selectedStockRow.display_name || selectedStockRow.name)) ||
                        effectiveStockId
                    ),
                  })
                )
              )
            )}

            {/* ── P23.2：两个买卖按钮**搬到下面那张「下单卡」**了 ──
            （按钮必须和滑块、预览数字**同屏可见**，所以它自己一张卡，
            永远贴在右列最下面，不会被精调件挤走。）── */}

            {/* ── 实时预览 + 直输框（两列，跟上面的按钮对齐）─────────
                P13：预览下面各挂两个输入框 —— **股数** 与 **金额**。
                玩家可以直接说"我要买 10 股"或"我要买 100 块"，系统
                **反推**出仓位比例并同步滑块（所以滑块永远只有一个
                事实来源：比例）。

                输入框用宿主**原生** ``Input``（自己造 input 就要自己
                写样式 = 自己写危险 CSS）。金额那一栏用文本型：数字型
                输入框会把"84."这种中间态吃掉，玩家根本打不出小数。 */}
            {tradeFineTuneOpen ? (
            <Grid cols={2} gap={8} className="rrm-trade-previews">
              <Stack gap={4} className="rrm-trade-preview-col">
                <Text>
                  {t("panel.trade_buy_preview", {
                    amount: buyAmount,
                    cost: buyCost.toFixed(2),
                    cash: buyCashAfter.toFixed(2),
                  })}
                </Text>
                {h(
                  "div",
                  {
                    className: "rrm-trade-inputs",
                    // 离开输入框 ⇒ 草稿作废，框里显示**实际**成交的那一组数。
                    // （"我要买 20 块"但一股就 11.77 ⇒ 真正花的是 11.77。）
                    //
                    // **用 focusout 不用 blur** —— 宿主 UI Kit 是**自研迷你运行时**
                    // （onX → addEventListener(x)），而 `blur` **不冒泡**：
                    // 挂在容器上的 onBlur 永远等不到子输入框的失焦。
                    // `focusout` 会冒泡，才是这里该用的那个。
                    onFocusout: (event: any) => {
                      const next = event && event.relatedTarget
                      if (next && event.currentTarget && event.currentTarget.contains(next)) {
                        return
                      }
                      setBuySharesDraft(null)
                      setBuyMoneyDraft(null)
                    },
                  },
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "number",
                      min: 0,
                      step: 1,
                      value: buySharesShown,
                      placeholder: t("panel.trade_input_shares"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onBuySharesInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_shares")),
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "text",
                      value: buyMoneyShown,
                      placeholder: t("panel.trade_input_money"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onBuyMoneyInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_money"))
                )}
              </Stack>
              <Stack gap={4} className="rrm-trade-preview-col">
                <Text>
                  {t("panel.trade_sell_preview", {
                    amount: sellAmount,
                    income: sellIncome.toFixed(2),
                    cash: sellCashAfter.toFixed(2),
                  })}
                </Text>
                {h(
                  "div",
                  {
                    className: "rrm-trade-inputs",
                    // 同上：离开输入框 ⇒ 吸附到**实际**回款（focusout 会冒泡）。
                    onFocusout: (event: any) => {
                      const next = event && event.relatedTarget
                      if (next && event.currentTarget && event.currentTarget.contains(next)) {
                        return
                      }
                      setSellSharesDraft(null)
                      setSellMoneyDraft(null)
                    },
                  },
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "number",
                      min: 0,
                      step: 1,
                      value: sellSharesShown,
                      placeholder: t("panel.trade_input_shares"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onSellSharesInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_shares")),
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "text",
                      value: sellMoneyShown,
                      placeholder: t("panel.trade_input_money"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onSellMoneyInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_money"))
                )}
              </Stack>
            </Grid>
            ) : null}

            {/* ── P14.0：计划买入台账（多标的）────────────────────────
                玩家可以给**好几只**标的排计划。这一栏把"打算花多少"
                摊开给玩家看：每只排了多少、总额多少。
                别的标的占掉的额度会**截断**买入滑条 ——
                拉扯的阻力感，就是极限的提示。

                P21.0：前两关**不玩**这一套 ⇒ 整块不渲染
                （而且台账占掉的额度也一起归零，见 othersBudget）——
                藏起来的东西不能还在暗中起作用。 */}
            {showPlanLedger ? (
              tradeFineTuneOpen ? (
            <Stack gap={6} className="rrm-plan">
              <Inline gap={8} align="center" justify="space-between">
                <Text>{t("panel.plan_title")}</Text>
                <Text>
                  {t("panel.plan_total", {
                    total: planTotal.toFixed(2),
                    cash: cash.toFixed(2),
                  })}
                </Text>
              </Inline>
              {planRows.length > 0
                ? h(
                    "div",
                    {
                      style: {
                        display: "flex",
                        flexDirection: "column",
                        gap: "4px",
                      },
                    },
                    planRows.map((row) =>
                      h(
                        "div",
                        {
                          className: "rrm-plan-row",
                          style: {
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: "8px",
                            fontSize: "12px",
                          },
                        },
                        h("span", { style: { color: "var(--text)" } }, row.name),
                        h(
                          "span",
                          { style: { color: "var(--muted)" } },
                          `${row.budget.toFixed(2)} ${t("panel.trade_unit_money")}`
                        ),
                        h(
                          "button",
                          {
                            type: "button",
                            className: "rrm-plan-drop",
                            style: {
                              border: "1px solid rgba(142, 171, 219, 0.35)",
                              background: "transparent",
                              color: "var(--muted)",
                              borderRadius: "6px",
                              padding: "0 7px",
                              cursor: "pointer",
                              fontSize: "12px",
                              lineHeight: "1.5",
                            },
                            onClick: () => dropPlan(row.id),
                          },
                          "×"
                        )
                      )
                    )
                  )
                : h(Text, null, t("panel.plan_empty"))}
            </Stack>
              ) : null
            ) : null}
          </ScrollArea>
        </Card>

        {/* ── P23.2：日常动作（分析 / 领工资 / 网贷 / 跳过本日）────────
            单独一张卡：这些是**每天一次**的动作，不是每分每秒都要看的，
            但它们必须**一直够得着**（教程要圈到「借网贷」那个按钮），
            所以留在面板里，只是收成一小排。 */}
        <Card className="rrm-daily-actions" title={t("panel.daily_actions")}>
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
                className="rrm-mini rrm-anchor-loan-button"
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

            {/* ── P27.0：买报纸 ─────────────────────────────────────────
                教程里那句「后面你得自己买报纸了」的兑现 ——
                报纸 = 一次分析机会。新手前两档白拿，后面按天价自掏。── */}
            {actionById("buy_report") ? (
              <Inline gap={8} wrap align="center">
                <Button
                  className="rrm-mini"
                  disabled={busy}
                  onClick={() => {
                    void buyReport()
                  }}
                >
                  {t("panel.report_buy")}
                </Button>
                <Text>
                  {state.report_free
                    ? t("panel.report_free")
                    : t("panel.report_hint", {
                        price: `${Number(state.report_price || 0).toFixed(0)} ${t(
                          "panel.trade_unit_money"
                        )}`,
                      })}
                </Text>
              </Inline>
            ) : null}

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
        </Card>

        {/* ── P27.1：资产迷你折线图**并进 K 线卡**了 ─────────────────
            （K 线正下方 ⇒ 图表区连成一整块深色画布。）── */}

        {/* ── P27.1：下单卡**搬到页面底部的操作台**了 ─────────────────
            三键并排（掏钱买 / 割肉卖 / 做空），上面紧跟一条
            「杠杆 / 标的 / 保证金」输入条；文字说明全搬去了【更多】。
            （下面 ``</Split>`` 之后那一整条就是它。）── */}

        {/* ── P23.2：名册 / 新闻 / 猫娘状态 / 弹幕 **都收进【更多】弹窗**了 ──
            （局中只留核心：名字、天数、钱、K 线、按钮。
              这些是**不需要每分每秒都看**的东西，见文件末尾的弹窗。）── */}

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
          </Stack>
        </Split>

        {/* ── P27.1：底部操作台（整条压在下沿）──────────────────────────
            三键并排（掏钱买 / 割肉卖 / 做空），上面紧跟一条
            「杠杆 / 标的 / 保证金」输入条。
            「文字说明」全部搬去了【更多】弹窗 ——
            屏幕上只留**能点的**和**能看的**。

            为什么搬到全宽：右列只有 40% 宽，三键挤在里面每个都不够
            按；摊到整条底边，每个键都有整屏那么宽（``Grid`` 三等分，
            天生等宽，不受按钮外壳包裹层影响）。

            ⚠ 这张卡的 class **必须原样**是 ``rrm-anchor-trade-console``
            （后面不许追加任何 class）—— 教程按这个 class 圈焦点。── */}
        <Card className="rrm-anchor-trade-console" title={t("panel.trade")}>
          <Stack gap={8}>
            {/* ── 杠杆 / 标的 / 保证金输入条（紧贴三键上方）────────────
                口径（倍率表 / 最小保证金）**全部来自载荷**
                （``short_options``）⇒ 面板不写死。

                P28.0：街机档**整条不渲染**（连同下面的「做空」键）——
                街机档只留掏钱买 / 割肉卖两键。── */}
            {arcadeSimple ? null : h(
              "div",
              { className: "rrm-short-panel" },
              h("span", { className: "rrm-short-label" }, t("panel.short_leverage")),
              h(Select, {
                value: String(effectiveShortLeverage),
                options: shortLeverageOptions,
                disabled: busy,
                onChange: (value: any) => setShortLeverage(String(value)),
              }),
              h("span", { className: "rrm-short-label" }, t("panel.short_stock")),
              h(Select, {
                value: effectiveShortStockId,
                options: stockOptions,
                disabled: busy,
                onChange: (value: any) => setShortStock(String(value)),
              }),
              h("span", { className: "rrm-short-label" }, t("panel.short_margin")),
              h(Input, {
                className: "rrm-trade-input",
                type: "number",
                min: 0,
                step: 1,
                value: shortMarginDraft,
                placeholder: t("panel.short_margin"),
                disabled: busy,
                onChange: (value: any) => {
                  setShortMarginDraft(String(value))
                },
              }),
              h(
                "span",
                { className: "rrm-short-count" },
                t("panel.short_open_count", { count: shortPositions.length })
              )
            )}
            {/* ── 三键并排（掏钱买 / 割肉卖 / 做空）────────────────────
                P12：三个键都用 ``Button`` + ``callAction`` ——
                ``ActionButton`` 既不支持 ``disabled``，也进不了
                P11.1 的全局请求锁（狂点会绕过「点击即禁用」）。

                P28.0：街机档**只剩两个键**（掏钱买 / 割肉卖）——
                「做空」是纯爽局之外的东西，街机档不玩。
                键位行本身一个字不动（``cols={3}`` 是既有红线），
                两列的口径由根节点的 ``rrm-arcade-simple`` 在 CSS 里改。── */}
            <Grid cols={3} gap={8} className="rrm-trade-row">
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
              {arcadeSimple ? null : (
              <Button
                className="rrm-pill rrm-pill-short"
                disabled={busy}
                onClick={() => {
                  void executeShort()
                }}
              >
                {t("panel.short_execute")}
              </Button>
              )}
            </Grid>
          </Stack>
        </Card>

        {/* ── P23.2：【更多】弹窗 ─────────────────────────────────────
            群友名册 / 当天新闻 / 猫娘公开状态 / 实时弹幕 ——
            这些**不是每一拍都要看**的东西，局中面板只留核心：
            名字、天数、钱、K 线、按钮。

            弹窗用宿主**原生** ``Modal``：全屏遮罩是**宿主自己的**
            （``position: fixed`` 是宿主写的，不在我们这份代码里），
            我们一个危险 CSS 都不用写。 */}
        <Modal
          open={extraOpen}
          title={t("panel.more_info")}
          onClose={() => {
            setExtraOpen(false)
          }}
          size="full"
        >
          <Stack gap={10}>
            {/* ── P27.1：操作说明（**冗余文字全部折叠到这里**）────────────
                局中屏幕上只留**能点的**和**能看的**：三键、行情、钱。
                所有「这是什么 / 怎么用」的说明文字都收进这个弹窗，
                一点【更多】就能看到，不占一屏。── */}
            <Card title={t("panel.more_trade_help")}>
              <Stack gap={8}>
                {/* 三个键下面那行小字（设计图里的副标题层次）。
                    P28.0：街机档没有「做空」⇒ 那一格小字也不给。 */}
                <Inline gap={8} className="rrm-pill-captions">
                  <span className="rrm-pill-caption rrm-pill-caption-buy">
                    {t("panel.trade_execute_buy_sub")}
                  </span>
                  <span className="rrm-pill-caption rrm-pill-caption-sell">
                    {t("panel.trade_execute_sell_sub")}
                  </span>
                  {arcadeSimple ? null : (
                  <span className="rrm-pill-caption rrm-pill-caption-short">
                    {t("panel.short_execute_sub")}
                  </span>
                  )}
                </Inline>
                <Text>{t("panel.trade_position_hint")}</Text>
                <Text>{t("visual.buy_all_hint")}</Text>
                <Text>{t("visual.sell_all_hint")}</Text>
              </Stack>
            </Card>

            {/* ── P27.1：做空规则 + 手上的空单 ───────────────────────────
                局中只留「已开 N 单」在输入条上；规则与明细都收在这里。

                P28.0：街机档没有做空 ⇒ 整张卡不渲染。── */}
            {arcadeSimple ? null : (
            <Card title={t("panel.short_title")}>
              <Stack gap={8}>
                <Text>{t("panel.short_hint")}</Text>
                <Text>{t("panel.short_positions")}</Text>
                {shortPositions.length > 0 ? (
                  h(
                    "div",
                    {
                      style: {
                        display: "flex",
                        flexDirection: "column",
                        gap: "4px",
                      },
                    },
                    shortPositions.map((row) =>
                      h(
                        "div",
                        {
                          key: String(row.stock_id || ""),
                          style: {
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: "8px",
                            fontSize: "12px",
                          },
                        },
                        h(
                          "span",
                          {},
                          t("panel.short_row", {
                            name: String(row.stock_id || ""),
                            leverage: Number(row.leverage || 1),
                            margin: Number(row.margin || 0).toFixed(2),
                            left: Number(row.margin_left || 0).toFixed(2),
                          })
                        ),
                        h(
                          "span",
                          {},
                          t("panel.short_pnl", {
                            pnl: Number(row.pnl || 0).toFixed(2),
                          })
                        )
                      )
                    )
                  )
                ) : (
                  h(Text, null, t("panel.short_empty"))
                )}
              </Stack>
            </Card>
            )}

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
                        {/* P28.0：街机档的新闻**只留分类 + 详情键** ——
                            那行「局中只看得到分类…」的解释是多余的。 */}
                        {arcadeSimple ? null : <Text>{t("panel.news_hint")}</Text>}
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
                      {/* P28.0：街机档不挂群友别称 / 头衔（多余的陪衬信息）。 */}
                      {newsDetail.roster_alias && !arcadeSimple ? (
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

            {/* ── P26.0：群友随机事件（天降 Token / 免费内参）──────────
                内参的**正文不在载荷里** ⇒ 面板只显示一枚 buff 标签，
                内容只在 Yui 那边（她照着内参吐槽）。 */}
            <Card title={t("panel.community_events")}>
              <Stack gap={8}>
                {community.length === 0 ? (
                  <Text>{t("panel.community_events_empty")}</Text>
                ) : (
                  <List<CommunityEvent>
                    items={community}
                    render={(item) => (
                      <Inline gap={8} align="center" wrap>
                        <StatusBadge tone="info" label={item.kind_label} />
                        <Text>{item.title}</Text>
                        {item.buff_tag ? (
                          <StatusBadge tone="warning" label={item.buff_tag} />
                        ) : null}
                        {item.detail ? <Text>{item.detail}</Text> : null}
                      </Inline>
                    )}
                  />
                )}
              </Stack>
            </Card>

            {/* ── ⑨ 猫娘公开状态 ───────────────────────────────────────
                P28.0：街机档**不显示**这一块（猫娘徽标在街机档是噪音）。── */}
            {catgirl && !arcadeSimple ? (
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

            {/* ── P7.2 ⑪：猫娘实时弹幕框 ─────────────────────────────── */}
            {danmakuCard}
          </Stack>
        </Modal>
        {loanModal}
      </Stack>
    </Page>
  )
}
