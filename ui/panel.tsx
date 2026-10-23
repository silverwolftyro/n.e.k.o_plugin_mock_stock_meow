// 反向暴富模拟器 · 市场面板（P5 完整版 + P7 节目效果版）
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
//      所以 K 线是用 div 拼出来的（见 `Kline`）。
//   2. `CommonProps` 里**没有** `style` → 需要内联样式的地方直接用
//      导出的 `h()`（它的 props 是 `any`）。
//   3. `style` 传的是**对象**（运行时逐键写 `dom.style[key]`），
//      **不是** CSS 字符串；CSS 自定义属性（`--x`）用 `setProperty` 也支持。
//
// P7 的四个模块全在这个文件里（**引擎零改动**，视觉全部落在面板）：
//   * 病危特效：红色高频闪烁 + 💀/🩸；
//   * 荒诞弹窗：赛博网贷广告（`Modal` 关不掉，只能借款或破产）；
//   * Emoji 海结算：终局铺满 🤡 / 🪦；
//   * 按钮漂移：特制版才开，5% 概率跑掉；
//   * 社区名册 + 猫娘弹幕悬浮窗 + 家道中落的视觉降级三档。
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
  NumberInput,
  Page,
  Progress,
  Select,
  Stack,
  StatCard,
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
  price: number
  prev_price: number
  change: number
  tag: string
  tag_label: string
  tag_color: string
  alive: boolean
  kline?: KlinePoint[]
}

type Position = {
  stock_id: string
  name: string
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
 * P7 自定义样式。
 *
 * 宿主运行时用 `document.createElement` 建元素，`style` 走**对象**逐键赋值，
 * CSS 自定义属性用 `setProperty`。这里注入一份 `<style>`（`h("style", null, css)`
 * —— 子节点会变成 text node），拿到 keyframes 与伪元素。
 */
const RRM_CSS = `
@keyframes rrm-panic {
  0%, 100% { background-color: rgba(220, 38, 38, 0.08); }
  50% { background-color: rgba(220, 38, 38, 0.40); }
}
@keyframes rrm-glitch {
  0%, 91%, 100% { opacity: 1; }
  93% { opacity: 0.30; }
  95% { opacity: 1; }
  97% { opacity: 0.45; }
}
@keyframes rrm-crack {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 0.26; }
}
@keyframes rrm-danmaku-in {
  from { opacity: 0; transform: translateY(16px) scale(0.96); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.rrm-critical { animation: rrm-panic 0.42s ease-in-out infinite; border-radius: 12px; }
.rrm-stage-rich .neko-card { border-color: #d4af37 !important; }
.rrm-stage-rich .neko-stat { border-color: rgba(212, 175, 55, 0.55) !important; }
.rrm-stage-broke { filter: grayscale(0.82) contrast(0.94) brightness(0.97); }
.rrm-stage-broke .neko-card { border-color: #4b5563 !important; }
.rrm-stage-dying { animation: rrm-glitch 7s steps(1, end) infinite; }
.rrm-stage-dying .neko-card { position: relative; }
.rrm-stage-dying .neko-card::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background:
    linear-gradient(112deg, transparent 47%, rgba(0, 0, 0, 0.92) 47.6%, transparent 48.4%),
    linear-gradient(68deg, transparent 61%, rgba(0, 0, 0, 0.78) 61.5%, transparent 62.2%);
  animation: rrm-crack 3.2s ease-in-out infinite;
}
.rrm-danmaku { position: sticky; bottom: 10px; z-index: 40; }
.rrm-danmaku-box {
  animation: rrm-danmaku-in 0.28s ease-out;
  border: 1px solid rgba(244, 63, 94, 0.55);
  background: rgba(14, 9, 13, 0.93);
  border-radius: 10px;
  padding: 8px 12px;
}
.rrm-emoji-sea { font-size: 20px; line-height: 1.25; letter-spacing: 1px; }
.rrm-titlebar { user-select: none; }
.rrm-drift { transition: transform 0.12s ease-out, opacity 0.12s ease-out; }
.rrm-titlebar-hold { opacity: 0.85; }
`

function money(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  return Number(value).toFixed(2)
}

function percent(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  return `${(Number(value) * 100).toFixed(1)}%`
}

/** K 线：用 div 拼出来的迷你蜡烛图（宿主画不了 SVG）。 */
function Kline(props: {
  points: KlinePoint[]
  emptyText: string
  /** P7：濒死档变成"心电图暗红"。 */
  ecg?: boolean
}) {
  const points = props.points || []
  if (points.length === 0) {
    return h("div", null, h("span", null, props.emptyText))
  }
  const prices = points.map((point) => point[1])
  const max = Math.max.apply(null, prices)
  const min = Math.min.apply(null, prices)
  const span = max - min || 1
  const height = 116
  const up = props.ecg ? "#7f1d1d" : "#15803d"
  const down = props.ecg ? "#450a0a" : "#b91c1c"
  const bars = points.map((point, index) => {
    const tick = point[0]
    const price = point[1]
    const barHeight = Math.max(2, Math.round(((price - min) / span) * (height - 10)))
    const previous = index > 0 ? points[index - 1][1] : price
    const color = price >= previous ? up : down
    return h("div", {
      key: tick,
      title: `tick ${tick} · ${price.toFixed(4)}`,
      style: {
        width: "4px",
        height: `${barHeight}px`,
        marginRight: "1px",
        background: color,
        borderRadius: "1px",
        boxShadow: props.ecg ? "0 0 4px rgba(127, 29, 29, 0.9)" : "none",
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
        padding: "4px 0",
        overflowX: "auto",
      },
    },
    ...bars
  )
}

/**
 * P7：会跑掉的按钮（**只在直播特制版开**）。
 *
 * 5% 概率在鼠标靠近时**偏移**，于是玩家的点击落在容器上而不是按钮上 ——
 * 动作**不会**被触发（这是"点空了"的本意），只弹一句提示；下一次点击恢复。
 */
function DriftButton(props: {
  action?: HostedAction
  label: string
  values?: Record<string, any>
  tone?: Tone
  drift: boolean
  driftRate: number
  onMiss: () => void
}) {
  const [shifted, setShifted] = useState(false)
  const armed = useRef(true)

  const maybeDrift = () => {
    if (!props.drift || !armed.current || shifted) return
    if (Math.random() < props.driftRate) {
      armed.current = false
      setShifted(true)
    }
  }

  const onWrapperClick = (event: any) => {
    if (!shifted) return
    if (event && typeof event.stopPropagation === "function") event.stopPropagation()
    props.onMiss()
    setShifted(false)
    armed.current = true
  }

  return h(
    "span",
    {
      style: {
        display: "inline-block",
        position: "relative",
        padding: shifted ? "0 16px" : "0",
      },
      onClick: onWrapperClick,
    },
    h(
      "span",
      {
        className: "rrm-drift",
        style: {
          display: "inline-block",
          transform: shifted ? "translate(14px, -6px)" : "translate(0, 0)",
          opacity: shifted ? 0.45 : 1,
        },
        onMouseEnter: maybeDrift,
        onPointerEnter: maybeDrift,
      },
      h(ActionButton, {
        action: props.action,
        label: props.label,
        values: props.values,
        tone: props.tone,
      })
    )
  )
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, actions } = props

  const [selectedStock, setSelectedStock] = useLocalState<string>("rrm.selected_stock", "")
  const [sellStock, setSellStock] = useLocalState<string>("rrm.sell_stock", "")
  const [buyAmount, setBuyAmount] = useState<number | "">("")
  const [sellAmount, setSellAmount] = useState<number | "">("")
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
  // ── P7：导演台（长按标题栏）、网贷弹窗、弹幕 ──────────────────────
  const [directorOpen, setDirectorOpen] = useState(false)
  const [holdHint, setHoldHint] = useState(false)
  const [loanModalOpen, setLoanModalOpen] = useState(false)
  const [loanModalDay, setLoanModalDay] = useState<number>(-1)
  const holdTimer = useRef<any>(null)

  const visual = state.visual || {}
  const stage = visual.stage || "rich"
  const critical = Boolean(visual.critical)
  const directorEnabled = Boolean((visual.director || {}).enabled)
  const danmakuList = visual.danmaku || []
  const rosterList = visual.roster || []

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
  }, [state.bill_gap, state.day, state.stage, loanModalDay])

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
    .map((stock) => ({ value: stock.id, label: `${stock.name}（${stock.id}）` }))

  const positionOptions = positions.map((position) => ({
    value: position.stock_id,
    label: t("panel.position_sellable", {
      name: position.name,
      sellable: position.sellable,
    }),
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
  const stageLabel =
    stage === "dying"
      ? t("visual.stage_dying")
      : stage === "broke"
        ? t("visual.stage_broke")
        : t("visual.stage_rich")

  // ── P7：终局 Emoji 海 ───────────────────────────────────────────
  const emojiSea = useMemo(() => {
    const size = Math.max(50, Number(visual.emoji_sea_size || 60))
    const items: string[] = []
    for (let index = 0; index < size; index += 1) {
      items.push(index % 2 === 0 ? "🤡" : "🪦")
    }
    return items
  }, [visual.emoji_sea_size])

  // ── P7：标题栏（长按露出导演台）─────────────────────────────────
  const titleBar = h(
    "div",
    {
      className: "rrm-titlebar",
      style: {
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "8px",
        padding: "6px 10px",
        border: "1px dashed rgba(148, 163, 184, 0.45)",
        borderRadius: "10px",
        background: "rgba(15, 23, 42, 0.35)",
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
          {
            className: holdHint ? "rrm-titlebar-hold" : "",
            style: { fontSize: "12px", opacity: holdHint ? 0.9 : 0.35 },
          },
          holdHint ? t("visual.director_hold") : t("visual.director_hint")
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
            h(Button, {
              onClick: () => setDirectorOpen(false),
              children: [t("visual.director_closed")],
            })
          )
        )
      )
    : null

  // ── P7：猫娘弹幕悬浮窗 ──────────────────────────────────────────
  const danmakuOverlay =
    danmakuList.length > 0
      ? h(
          "div",
          { className: "rrm-danmaku" },
          h(
            "div",
            { className: "rrm-danmaku-box" },
            h(
              "div",
              { style: { fontSize: "12px", opacity: 0.7, marginBottom: "2px" } },
              t("visual.danmaku_title")
            ),
            h(
              "div",
              { key: String(danmakuList[danmakuList.length - 1].id || 0) },
              danmakuList[danmakuList.length - 1].text || ""
            )
          )
        )
      : null

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
        <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
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
            {danmakuOverlay}
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
      <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          <div className={`rrm-stage-${stage} ${critical ? "rrm-critical" : ""}`}>
            <Stack gap={12}>
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
            </Stack>
          </div>
          {danmakuOverlay}
        </Stack>
      </Page>
    )
  }

  // ── 没开局 ───────────────────────────────────────────────────────
  if (state.stage !== "playing") {
    return (
      <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          <Card>
            <Stack gap={12}>
              <Alert tone="info" message={state.notice || t("panel.idle")} />
              <Text>{t("panel.idle_hint")}</Text>
            </Stack>
          </Card>
          {danmakuOverlay}
        </Stack>
      </Page>
    )
  }

  // ── P7 模块二 ②：荒诞弹窗（赛博网贷广告）─────────────────────
  const loanModal = h(Modal, {
    open: loanModalOpen,
    title: t("visual.loan_modal_title"),
    // **点遮罩 / 按 ESC 都关不掉** —— 只能点按钮。
    closeOnBackdrop: false,
    closeOnEscape: false,
    size: "md",
    children: h(
      Stack,
      { gap: 12 },
      h(Text, null, t("visual.loan_modal_body")),
      h(Alert, { tone: "danger", message: t("visual.loan_modal_note") })
    ),
    footer: h(
      "div",
      { className: "neko-button-group" },
      state.mode === "infinite"
        ? h(ActionButton, {
            action: actionById("daily_loan"),
            label: t("visual.loan_modal_confirm"),
            tone: "danger",
            onResult: () => setLoanModalOpen(false),
            onError: (error: any) =>
              showToast(String((error && error.message) || error), "danger"),
          })
        : h(ActionButton, {
            action: actionById("declare_bankrupt"),
            label: t("visual.loan_modal_classic"),
            tone: "danger",
            onResult: () => setLoanModalOpen(false),
            onError: (error: any) =>
              showToast(String((error && error.message) || error), "danger"),
          })
    ),
  })

  const driftEnabled = directorEnabled

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Stack gap={12}>
        {h("style", null, RRM_CSS)}
        {titleBar}
        {directorConsole}
        <div className={`rrm-stage-${stage} ${critical ? "rrm-critical" : ""}`}>
          <Stack gap={12}>
            {/* ① 顶部状态条 */}
            <Card>
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
                {/* P7：病危标记 + 家道档位 */}
                {critical ? (
                  <StatusBadge
                    tone="danger"
                    label={`${visual.critical_emoji || "🩸"} ${t("visual.critical")}`}
                  />
                ) : null}
                <StatusBadge tone="default" label={stageLabel} />
              </Inline>
            </Card>

            {/* ② 财务三卡 + ③ 明日账单条 */}
            <Grid cols={3} gap={12}>
              <StatCard
                label={`${cashLabel}${critical ? ` ${visual.critical_emoji || "🩸"}` : ""}`}
                value={money(state.cash)}
              />
              <StatCard label={t("panel.total_assets")} value={money(state.total_assets)} />
              <StatCard
                label={t("panel.distance_to_kill")}
                value={
                  state.distance_to_kill === null || state.distance_to_kill === undefined
                    ? "—"
                    : money(state.distance_to_kill)
                }
              />
            </Grid>

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

            {/* ④ 持仓列表 + ⑤ 集中度条 */}
            <Card title={t("panel.positions")}>
              <Stack gap={12}>
                <Progress
                  label={t("panel.concentration", { value: percent(state.concentration) })}
                  value={Math.round((state.concentration || 0) * 100)}
                />
                {positions.length === 0 ? (
                  <Text>{t("panel.no_positions")}</Text>
                ) : (
                  <DataTable<Position>
                    data={positions}
                    rowKey="stock_id"
                    emptyText={t("panel.no_positions")}
                    columns={[
                      { key: "name", label: t("panel.stock") },
                      { key: "amount", label: t("panel.amount") },
                      { key: "avg_cost", label: t("panel.cost"), render: (row) => money(row.avg_cost) },
                      { key: "price", label: t("panel.price"), render: (row) => money(row.price) },
                      {
                        key: "unrealized_pnl",
                        label: t("panel.pnl"),
                        render: (row) => money(row.unrealized_pnl),
                      },
                      { key: "sellable", label: t("panel.sellable") },
                      {
                        key: "weight",
                        label: t("panel.weight"),
                        render: (row) => percent(row.weight),
                      },
                      {
                        key: "tag",
                        label: t("panel.tag"),
                        render: (row) => (
                          <StatusBadge
                            tone={row.tag === "explosive" ? "danger" : row.tag === "stable" ? "success" : "default"}
                            label={row.tag_label}
                          />
                        ),
                      },
                    ]}
                  />
                )}
              </Stack>
            </Card>

            {/* ⑥ 标的行情表 */}
            <Card title={t("panel.market")}>
              <DataTable<Stock>
                data={stocks}
                rowKey="id"
                emptyText={t("panel.no_stocks")}
                columns={[
                  { key: "name", label: t("panel.stock") },
                  { key: "price", label: t("panel.price"), render: (row) => money(row.price) },
                  {
                    key: "change",
                    label: t("panel.change"),
                    render: (row) => percent(row.change),
                  },
                  {
                    key: "tag",
                    label: t("panel.tag"),
                    render: (row) => (
                      <StatusBadge
                        tone={row.tag === "explosive" ? "danger" : row.tag === "stable" ? "success" : "default"}
                        label={row.tag_label}
                      />
                    ),
                  },
                  {
                    key: "alive",
                    label: t("panel.status"),
                    render: (row) =>
                      row.alive ? (
                        <StatusBadge tone="success" label={t("panel.alive")} />
                      ) : (
                        <StatusBadge tone="danger" label={t("panel.delisted")} />
                      ),
                  },
                ]}
              />
            </Card>

            {/* ── P7 模块三 ①：社区名册 ─────────────────────────── */}
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

            {/* ⑦ K 线（P6 档 2：局中载荷只带前 20 只，其余按需拉取） */}
            <Card title={t("panel.kline")}>
              <Stack gap={12}>
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
                    </Inline>
                    {/* P7 模块四 ③：濒死档的 K 线变心电图暗红 */}
                    <Kline points={klinePoints} emptyText={t("panel.kline_empty")} ecg={stage === "dying"} />
                    {klineStock ? (
                      <Text>
                        {t("panel.kline_hint", {
                          name: klineStock.name,
                          price: money(klineStock.price),
                        })}
                      </Text>
                    ) : null}
                  </>
                )}
              </Stack>
            </Card>

            {/* ⑧ 操作区 */}
            <Card title={t("panel.trade")}>
              <Stack gap={12}>
                <Inline gap={8} wrap align="center">
                  <Text>{t("panel.stock")}</Text>
                  <Select
                    value={effectiveStockId}
                    options={stockOptions}
                    onChange={(value: any) => setSelectedStock(String(value))}
                  />
                </Inline>

                <Inline gap={8} wrap align="center">
                  <Text>{t("panel.amount")}</Text>
                  <NumberInput
                    value={buyAmount}
                    min={0}
                    step={1}
                    onChange={(value: any) => setBuyAmount(value)}
                  />
                  {/* P7 模块二 ④：特制版里「梭哈」按钮会跑掉 */}
                  <DriftButton
                    action={actionById("buy")}
                    label={t("action.buy")}
                    tone="primary"
                    values={{ stock_id: effectiveStockId, amount: Number(buyAmount) || 0 }}
                    drift={driftEnabled}
                    driftRate={0.05}
                    onMiss={() => showToast(t("visual.drift_miss"), "warning")}
                  />
                </Inline>

                <Inline gap={8} wrap align="center">
                  <Text>{t("panel.sell_amount")}</Text>
                  <Select
                    value={effectiveSellStockId}
                    options={positionOptions}
                    onChange={(value: any) => setSellStock(String(value))}
                  />
                  <NumberInput
                    value={sellAmount}
                    min={0}
                    step={1}
                    onChange={(value: any) => setSellAmount(value)}
                  />
                  <ActionButton
                    action={actionById("sell")}
                    label={t("action.sell")}
                    values={{ stock_id: effectiveSellStockId, amount: Number(sellAmount) || 0 }}
                  />
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
                  <DriftButton
                    action={actionById("daily_loan")}
                    label={t("daily.loan")}
                    tone="danger"
                    drift={driftEnabled}
                    driftRate={0.05}
                    onMiss={() => showToast(t("visual.drift_miss"), "warning")}
                  />
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

            {/* ⑨ 猫娘公开状态 */}
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

            {/* ⑩ 当天新闻（局中只有中性分类，标题正文要点开才给） */}
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

            {/* ⑪ 二道贩子弹窗（方案 A：引擎零改动，面板弹窗征求同意） */}
            {showPawnbroker && pending ? (
              <Card title={t("panel.pawnbroker")}>
                <Stack gap={8}>
                  <Text>
                    {t("panel.pawnbroker_hint", {
                      amount: pending.amount || 0,
                      reason: pending.reason || "",
                    })}
                  </Text>
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
        </div>
        {danmakuOverlay}
        {loanModal}
      </Stack>
    </Page>
  )
}
