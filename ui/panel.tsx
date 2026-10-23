// 反向暴富模拟器 · 市场面板（P7.2 极简重构版）
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
// 0.8.1 起的**硬规矩**（Steam 宿主 0.9.0 上 0.8.0 把面板 UI 整个搞崩了）：
//   * 面板**只用宿主组件的默认样式** —— 不碰 opacity / filter / animation /
//     position / 背景 / 伪元素；
//   * 卡片必须是 `Stack` 的**直接** flex item（和 P6 已验证的骨架一致），
//     中间**不许**再塞 `<div>` 包裹层；
//   * 自定义 CSS 只剩「盒模型 + 溢出收敛 + 关掉入场动画 + 字体档位」这类
//     **不改变布局**的声明（见 RRM_CSS）；
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
// 局中隐藏（P4 §14 / P5 方案 B）：本面板**不显示** heat、反转日、新闻真假。
// 载荷里没有的东西，面板也拿不到 —— 面板只渲染 `state` 里的白名单字段。
import {
  ActionButton,
  Alert,
  Button,
  Card,
  DataTable,
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

type KlinePoint = [number, number]

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

/** 涨绿跌红（和 K 线、结算卡片一个口径）。 */
const TREND_UP = "#15803d"
const TREND_DOWN = "#b91c1c"

/**
 * 0.8.1 防御性基线 + P7.2 字体档位 —— **只做收敛，绝不做任何视觉效果**。
 *
 * 0.8.0 在 Steam 宿主（0.9.0）上把面板 UI 整个搞崩了，根因有**五**条：
 *
 *   1. `.neko-card::after` 的 `position:absolute; inset:0` 加两条对角
 *      `linear-gradient` ⇒ 面板上出现**巨大的交叉 X 线条**，直接盖在表格上。
 *   2. 祖先容器上的动画改的是**祖先容器的 opacity** ⇒ 整个面板泛白半透明。
 *   3. 祖先容器上的 `filter: grayscale(...)` ⇒ filter 会给祖先建立
 *      containing block，宿主的 flex / grid 布局随之错位。
 *   4. 自定义的 `<div>` 包裹层把卡片从 `Stack` 的 flex item 变成了嵌套
 *      block ⇒ 间距消失、挤成一团。
 *   5. **宿主自己的** `.neko-page` / `.neko-card` 都带入场动画（从
 *      `opacity: 0` 起步），而宿主每次刷新上下文都会**重建 iframe**，
 *      于是这段入场动画**每次都从头重放** —— 面板长期停在半透明状态。
 *      宿主的 `styles.css` 里其实留了官方逃生口
 *      （`@media (prefers-reduced-motion: reduce)` → `animation: none !important`），
 *      这里照搬它的口径，但**限定在本面板的根节点内**。
 *
 * 因此这里的规矩是：
 *   * 面板**只用宿主组件的默认样式**，不碰 opacity / filter / 动画 /
 *     position / 背景 / 伪元素；
 *   * 自定义 CSS 只允许「盒模型 + 溢出收敛 + 关掉入场动画 + 字体档位」
 *     这类**不会改变布局**的声明；
 *   * 卡片必须是 `Stack` 的**直接** flex item（和 P6 已验证的骨架一致）。
 *
 * P7.2 新增的三档字体（**只改字体，不改布局**）：
 *   * 富二代 → 圆润体（幼圆 / YouYuan，缺了自动回退系统字体）；
 *   * 断供   → 细体（font-weight: 300）；
 *   * 濒死   → 中等字重（数字另有「偶尔闪红」，见 `flashOn`）。
 *
 * 宿主 `styles.css` 里本来就有 `* { box-sizing: border-box }` 与
 * `.neko-card { overflow: hidden }`，这里再声明一次是为了不依赖宿主版本。
 */
const RRM_CSS = `
*, *::before, *::after { box-sizing: border-box; }
.rrm-root, .rrm-root *, .rrm-root *::before, .rrm-root *::after { animation: none !important; }
.rrm-root .neko-stack > *, .rrm-root .neko-card-body > * { min-width: 0; }
.rrm-root .neko-key-value-value { overflow-wrap: anywhere; }
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
function Sparkline(props: { points: number[]; up: boolean }) {
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
    style: { display: "block" },
  })
}

/** K 线：用 div 拼出来的迷你蜡烛图（宿主画不了 SVG）。 */
function Kline(props: {
  points: KlinePoint[]
  emptyText: string
}) {
  const points = props.points || []
  if (points.length === 0) {
    return h("div", null, h("span", null, props.emptyText))
  }
  const prices = points.map((point) => point[1])
  const max = Math.max.apply(null, prices)
  const min = Math.min.apply(null, prices)
  const span = max - min || 1
  // P7.2：K 线是**主视觉**，所以更高、而且自适应铺满全宽。
  const height = 190
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
  const news = state.news_today || []
  const catgirl = state.catgirl || null
  const pending = state.pending_action || null
  const klineIds = state.kline_stock_ids || []

  const actionById = (id: string): HostedAction | undefined =>
    (actions || []).find((item) => item.id === id)

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
              onError: (error: any) =>
                showToast(String((error && error.message) || error), "danger"),
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_loan"),
              tone: "danger",
              values: { op: "loan" },
              onError: (error: any) =>
                showToast(String((error && error.message) || error), "danger"),
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_welfare"),
              tone: "danger",
              values: { op: "welfare" },
              onError: (error: any) =>
                showToast(String((error && error.message) || error), "danger"),
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_kill"),
              tone: "danger",
              values: { op: "kill" },
              onError: (error: any) =>
                showToast(String((error && error.message) || error), "danger"),
            }),
            // P7.2：赛博弹窗轰炸（关不掉的 Emoji 海 + 封锁操作 5 秒）。
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_boom"),
              tone: "danger",
              values: { op: "boom" },
              onError: (error: any) =>
                showToast(String((error && error.message) || error), "danger"),
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
        ? h(Text, null, t("visual.danmaku_empty"))
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
                    {t("panel.death_scene_skip")}
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

    return (
      <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          <Card title={t("panel.settlement")}>
            <Stack gap={12}>
              <Alert tone="info" message={t("panel.settlement_notice")} />
              <KeyValue
                items={[
                  { key: "mode", label: t("panel.mode"), value: report.mode_name || report.mode },
                  { key: "days", label: t("panel.days_survived"), value: report.days_survived },
                  { key: "trades", label: t("panel.trades"), value: report.trade_count },
                  { key: "reason", label: t("panel.death_reason"), value: report.death_reason || "—" },
                  { key: "title", label: t("panel.death_title"), value: report.death_title || "—" },
                  { key: "ending", label: t("panel.ending"), value: report.ending || "—" },
                  { key: "identity", label: t("panel.identity"), value: report.identity || "—" },
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
                      value: catgirlResult.death_reason || "—",
                    },
                  ]}
                />
              ) : null}
              {/* P5.5 改动 3：猫娘沉默旁白（在猫娘播报之后） */}
              {report.catgirl_silence ? <Text>{report.catgirl_silence}</Text> : null}
              <Text>{report.anti_fraud_notice || ""}</Text>
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
    return (
      <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          {boomModal}
          <Card>
            <Stack gap={12}>
              <Alert tone="info" message={state.notice || t("panel.idle")} />
              <Text>{t("panel.idle_hint")}</Text>
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
                <Inline gap={8} wrap align="center">
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
                <Kline points={klinePoints} emptyText={t("panel.kline_empty")} />
              </>
            )}
          </Stack>
        </Card>

        {/* ── P7.2 ②：傻子版第一屏 —— 三块超大数字 ─────────────────── */}
        <Grid cols={3} gap={12}>
          <BigStat
            label={assetsLabel}
            value={money(totalAssets)}
            hint={stageHint}
            color={flashColor}
            size={36}
          />
          <BigStat
            label={t("visual.pnl_title")}
            value={`${pnl > 0 ? "+" : ""}${money(pnl)}`}
            hint={`${t("visual.total_pnl")} · ${pnlHint}`}
            color={pnlColor}
            size={36}
          />
          <BigStat label={cashLabel} value={money(state.cash)} size={28} />
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

        {/* ── P7.2 ④：行情列表（极简 + 走势小折线 + 超大涨跌）───────── */}
        <Card title={t("visual.market_title")}>
          <DataTable<Stock>
            data={stocks}
            rowKey="id"
            emptyText={t("panel.no_stocks")}
            columns={[
              {
                key: "name",
                label: t("panel.stock"),
                render: (row) =>
                  h(
                    "span",
                    { style: { fontWeight: 700, fontSize: "14px" } },
                    row.display_name || row.name
                  ),
              },
              {
                key: "spark",
                label: t("visual.trend_title"),
                render: (row) =>
                  h(Sparkline, {
                    points: (row.spark || []).map((point) => point[1]),
                    up: Number(row.change || 0) >= 0,
                  }),
              },
              {
                key: "price",
                label: t("visual.price_title"),
                render: (row) =>
                  h(
                    "span",
                    { style: { fontWeight: 700, fontSize: "15px" } },
                    money(row.price)
                  ),
              },
              {
                key: "change",
                label: t("visual.change_title"),
                render: (row) =>
                  h(
                    "span",
                    {
                      style: {
                        fontSize: "26px",
                        fontWeight: 800,
                        color: changeColor(row.change),
                        whiteSpace: "nowrap",
                      },
                    },
                    changeText(row.change)
                  ),
              },
            ]}
          />
        </Card>

        {/* ── P7.2 ⑤：持仓列表（只留名称 / 市值 / 大号盈亏）───────── */}
        <Card title={t("visual.holdings_title")}>
          <Stack gap={12}>
            {positions.length === 0 ? (
              <Text>{t("panel.no_positions")}</Text>
            ) : (
              <DataTable<Position>
                data={positions}
                rowKey="stock_id"
                emptyText={t("panel.no_positions")}
                columns={[
                  {
                    key: "name",
                    label: t("panel.stock"),
                    render: (row) =>
                      h(
                        "span",
                        { style: { fontWeight: 700, fontSize: "14px" } },
                        row.display_name || row.name
                      ),
                  },
                  {
                    key: "market_value",
                    label: t("visual.price_title"),
                    render: (row) =>
                      h(
                        "span",
                        { style: { fontWeight: 700, fontSize: "18px" } },
                        money(row.market_value)
                      ),
                  },
                  {
                    key: "unrealized_pnl",
                    label: t("visual.pnl_title"),
                    render: (row) =>
                      h(
                        "span",
                        {
                          style: {
                            fontSize: "26px",
                            fontWeight: 800,
                            color:
                              Number(row.unrealized_pnl || 0) > 0.001
                                ? "var(--success)"
                                : Number(row.unrealized_pnl || 0) < -0.001
                                  ? "var(--danger)"
                                  : "var(--muted)",
                            whiteSpace: "nowrap",
                          },
                        },
                        `${Number(row.unrealized_pnl || 0) > 0 ? "+" : ""}${money(
                          row.unrealized_pnl
                        )}`
                      ),
                  },
                ]}
              />
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

        {/* ── P7.2 ⑥：操作区（一键梭哈 / 一键割肉）─────────────────── */}
        <Card title={t("panel.trade")}>
          <Stack gap={12}>
            <Inline gap={8} wrap align="center">
              <Select
                value={effectiveStockId}
                options={stockOptions}
                onChange={(value: any) => setSelectedStock(String(value))}
              />
              {buyAllAmount > 0 ? (
                <ActionButton
                  action={actionById("buy")}
                  label={t("visual.buy_all")}
                  tone="primary"
                  values={{ stock_id: effectiveStockId, amount: buyAllAmount }}
                />
              ) : (
                <Button
                  onClick={() => {
                    showToast(t("visual.no_money"), "warning")
                  }}
                >
                  {t("visual.buy_all")}
                </Button>
              )}
              <Text>{t("visual.buy_all_hint")}</Text>
            </Inline>

            <Inline gap={8} wrap align="center">
              <Select
                value={effectiveSellStockId}
                options={positionOptions}
                onChange={(value: any) => setSellStock(String(value))}
              />
              {sellAllAmount > 0 ? (
                <ActionButton
                  action={actionById("sell")}
                  label={t("visual.sell_all")}
                  tone="danger"
                  values={{ stock_id: effectiveSellStockId, amount: sellAllAmount }}
                />
              ) : (
                <Button
                  onClick={() => {
                    showToast(t("visual.nothing_to_sell"), "warning")
                  }}
                >
                  {t("visual.sell_all")}
                </Button>
              )}
              <Text>{t("visual.sell_all_hint")}</Text>
            </Inline>

            <Divider />

            <Inline gap={8} wrap align="center">
              <ActionButton
                action={actionById("analyze")}
                label={t("action.analyze")}
                values={{ stock_id: effectiveStockId }}
                onResult={(result: any) => {
                  if (result) setAnalysis(result as AnalysisResult)
                }}
                onError={(error: any) =>
                  showToast(String((error && error.message) || error), "danger")
                }
              />
              <Text>{t("panel.analyses_left", { count: state.analyses_left || 0 })}</Text>
            </Inline>

            <Inline gap={8} wrap align="center">
              <ActionButton action={actionById("daily_claim")} label={t("daily.claim")} />
              <ActionButton action={actionById("daily_loan")} label={t("daily.loan")} tone="danger" />
              <ActionButton action={actionById("daily_reject")} label={t("panel.reject")} />
              {/* P5.5 改动 6：跳过本日（无限模式专用，经典模式入口会报错） */}
              <ActionButton
                action={actionById("skip_day")}
                label={t("panel.skip_day")}
                onError={(error: any) =>
                  showToast(String((error && error.message) || error), "danger")
                }
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
