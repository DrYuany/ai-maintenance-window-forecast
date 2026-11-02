"""小一修槽位注册表：唯一事实来源。

纪律（改这个文件的三条硬规矩）：
1. **没有落点的槽位不许注册**。注册即承诺：校验器认、注入口编译得了、对账函数查得出来。
2. **注册项不全不许上线**。人话名、索引种类、默认值、落点、消费点、对账函数名——缺一项就在
   ``audit_registry()`` 里报出来。这一条沿用大一修 parameters.py 的教训。
3. **对账函数必须登记**。槽位族固定，所以「新加了族却忘了补对账」在结构上不可能发生——
   前提是对账函数是注册表的必填项，而不是另写一张表。

索引种类的两种性质，决定提示词与校验代码怎么写：
  * 离散：能从固定清单里挑，写错当场拒，还能在提示词里附上快照真实数字当护栏
  * 开放：要模型自己填内容（任务清单、日期区间），只能事后查，需单独解析代码
仅两类是开放的：任务清单（任意组合）、日期区间（任意起止）。形状不受影响——
不管点哪三个任务，写出来的还是同一句「加起来最多一个」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# ── 索引种类 ────────────────────────────────────────────────────────────
# 每个种类登记：提示词里怎么描述、它的取值是离散枚举还是开放填写。
INDEX_KINDS: dict[str, dict[str, str]] = {
    "group": {"说明": "按什么类别圈定一批任务", "性质": "混合"},
    "bucket": {"说明": "哪一段时间（按开工时间计）", "性质": "混合"},
    "task": {"说明": "点名一个任务编号", "性质": "离散"},
    "task_seq": {"说明": "有序任务序列，长度≥2", "性质": "开放"},
    "task_set": {"说明": "任务集合，长度≥2，无序", "性质": "开放"},
    "severity_pair": {"说明": "两个严重度层，如 高→中", "性质": "离散"},
    "metric": {"说明": "14 个已登记目标量之一", "性质": "离散"},
    "budget_口径": {"说明": "花钱的口径", "性质": "离散"},
    "cap_维度": {"说明": "被占用实例的类别", "性质": "离散"},
    "day": {"说明": "规划期内的一天", "性质": "离散"},
    "part": {"说明": "部件编号或名称", "性质": "离散"},
    "moment": {"说明": "规划期内的一个时刻", "性质": "开放"},
    "window": {"说明": "窗口编号", "性质": "离散"},
    "strategy": {"说明": "四种目标策略之一", "性质": "离散"},
    "项": {"说明": "改的是哪一个量（风速/浪高/电价）", "性质": "离散"},
    "端": {"说明": "窗口的开始还是结束", "性质": "离散"},
    "粒度": {"说明": "按天还是按窗口判定", "性质": "离散"},
}

# 可选索引的取值域：给了就校验，不给就按该维全改/默认处理。
OPTIONAL_ENUMS: dict[str, tuple[str, ...]] = {
    "项": ("风速", "浪高", "电价"),
    "端": ("开始", "结束"),
    "粒度": ("天", "窗口"),
    "严重度": ("高", "中", "低"),
    "海况": ("常态", "恶劣但安全"),
}

# 组（group）能取的类别。全部离散，除「任务清单」开放。
GROUP_KINDS = ("全部", "严重度", "部件", "风机", "任务", "任务清单", "作业线")
# 桶（bucket）能取的类别。「日期区间」开放。
BUCKET_KINDS = ("窗口", "某日", "日期区间")
# 预算口径：前两个内核原生（走 limits），后三个走注入口。
BUDGET_KINDS = ("现金", "总成本", "人工", "备件", "租船天")
# 容量维度：某桶内被占用的实例数。
CAP_DIMENSIONS = ("作业线", "风机", "部件")

# 目标量：内核 metrics 原有 11 个 + 人工/备件/船舶艘天（内核 metrics 加三行）。
METRIC_NAMES = (
    "总经济成本", "现金支出", "完成任务数", "最后完工时间", "完工时间和",
    "高严重完成数", "中严重完成数", "低严重完成数",
    "高严重完工时间和", "中严重完工时间和", "低严重完工时间和",
    "人工费", "备件费", "船舶艘天",
)

STRATEGY_NAMES = ("成本最低优先", "高风险优先抢修", "最早完工优先", "最多完成数优先")


@dataclass(frozen=True)
class Slot:
    """一个可写槽位族的完整登记。"""

    name: str
    group: str
    label: str                      # 人话名
    route: str                      # params|strategy|weights|limits|closure|data|timeline|partial
    index: tuple[tuple[str, str], ...] = ()   # ((索引名, 种类), ...)
    default: Any = None             # 不生效时的值：None = 不加约束
    default_note: str = ""          # 默认值的人话说明
    unit: str = ""
    value_type: str = "int"         # int|float|bool|enum|str|minutes
    bounds: tuple[float, float] | None = None
    consumption: str = "model"      # model（只重建模型）| candidates（要重生成候选）
    strength: str = "hard"          # hard|soft|both
    reconcile: str = ""             # 对账函数名，注册即承诺，不许空
    examples: tuple[str, ...] = ()
    note: str = ""
    optional: tuple[str, ...] = ()  # 可省略的索引维度（省略 = 该维全改/取默认）


# ── A 组：标量参数（走 overrides） ──────────────────────────────────────
# 槽位名 → 系统参数表口径。白名单来自 product_data.OVERRIDES，改动前先看那里。
PARAM_ALIASES: dict[str, str] = {
    "船租": "船租（元每艘每天）",
    "人工单价": "人工单价（元每人时）",
    "最多作业线数": "最多作业线数",
    "人员总上限": "人员总上限",
    "每天最多维修小时数": "每天最多维修小时数",
    "风机间转移小时数": "风机间转移小时数",
    "每天开工时间": "每天维修开始时间",
    "每天收工时间": "每天维修结束时间",
    "候选开工粒度": "候选开工粒度",
    "跨天暂停": "跨天暂停",
    "允许夜间转移": "允许夜间转移",
    "常态风速上限": "常态最大风速（米每秒）",
    "常态浪高上限": "常态最大有效波高（米）",
    "安全风速上限": "安全最大风速（米每秒）",
    "安全浪高上限": "安全最大有效波高（米）",
}

# 标量槽位单独一张表：它们没有索引维度，形状是「改一个系数」。
PARAM_SLOTS: dict[str, dict[str, Any]] = {
    "船租": {"单位": "元/艘/天", "类型": "float", "界": (0, 10_000_000), "消费点": "model"},
    "人工单价": {"单位": "元/人时", "类型": "float", "界": (0, 100_000), "消费点": "model"},
    "最多作业线数": {"单位": "条", "类型": "int", "界": (1, 100), "消费点": "model"},
    "人员总上限": {"单位": "人", "类型": "int", "界": (1, 100), "消费点": "model"},
    "每天最多维修小时数": {"单位": "小时", "类型": "float", "界": (0, 24), "消费点": "candidates"},
    "风机间转移小时数": {"单位": "小时", "类型": "float", "界": (0, 24), "消费点": "candidates"},
    "每天开工时间": {"单位": "HH:MM", "类型": "str", "界": None, "消费点": "candidates"},
    "每天收工时间": {"单位": "HH:MM", "类型": "str", "界": None, "消费点": "candidates"},
    "候选开工粒度": {"单位": "分钟", "类型": "int", "界": (1, 1440), "消费点": "candidates"},
    "跨天暂停": {"单位": "是/否", "类型": "bool", "界": None, "消费点": "candidates"},
    "允许夜间转移": {"单位": "是/否", "类型": "bool", "界": None, "消费点": "candidates"},
    "常态风速上限": {"单位": "米/秒", "类型": "float", "界": (0, 100), "消费点": "candidates"},
    "常态浪高上限": {"单位": "米", "类型": "float", "界": (0, 100), "消费点": "candidates"},
    "安全风速上限": {"单位": "米/秒", "类型": "float", "界": (0, 100), "消费点": "candidates"},
    "安全浪高上限": {"单位": "米", "类型": "float", "界": (0, 100), "消费点": "candidates"},
}


# ── B~J 组：约束族 ─────────────────────────────────────────────────────
SLOTS: dict[str, Slot] = {
    # B 组 · 目标
    "strategy": Slot(
        name="strategy", group="B", label="排程策略", route="strategy",
        default="成本最低优先", default_note="成本最低优先",
        value_type="enum", consumption="model", strength="both",
        reconcile="reconcile_strategy",
        examples=("按成本最低排", "整体按高风险抢修的思路排", "越早干完越好", "尽量多修几个"),
        note="只在用户要**整体切换排程风格**时用它（四选一）。"
             "「高危优先」「先保高严重」这类只点了一个偏好量的，落 weight（软），不要动 strategy",
    ),
    "weight": Slot(
        name="weight", group="B", label="目标权重", route="weights",
        index=(("量", "metric"),), default=0, default_note="0，即该量不参与权衡",
        value_type="float", bounds=(0, 1e6), consumption="model", strength="soft",
        reconcile="reconcile_weight",
        examples=("高危优先", "省钱比赶时间重要", "尽量压租船天数"),
        note="软偏好通道。用户说「必须/不许」时不要落这里（见 README 第六节硬软消歧）",
    ),
    # C 组 · 修多少
    "count_min": Slot(
        name="count_min", group="C", label="至少修几个", route="closure",
        index=(("组", "group"),), default=None, default_note="0，恒成立即不加约束",
        unit="个", value_type="int", bounds=(0, 999), consumption="model", strength="hard",
        reconcile="reconcile_count",
        examples=("至少修 10 个", "高危全都要修", "齿轮箱至少修 2 个",
                  "风机001 必须全修完", "任务018 一定得排进去", "这三个里至少挑一个"),
        note="桶恒为全程；要限定某段时间请落 bucket_min（见 README 边界）",
    ),
    "count_max": Slot(
        name="count_max", group="C", label="最多修几个", route="closure",
        index=(("组", "group"),), default=None, default_note="任务总数，等于不限制",
        unit="个", value_type="int", bounds=(0, 999), consumption="model", strength="hard",
        reconcile="reconcile_count",
        examples=("最多修 8 个", "风机003 最多排 2 个", "任务022 这轮先不修",
                  "这三个里最多挑一个"),
        note="拧到 0 = 整类不修；会触发部分排程模式（判据只有一份，见 apply.needs_partial）",
    ),
    # D 组 · 何时修
    "bucket_min": Slot(
        name="bucket_min", group="D", label="某时段内至少开工几个", route="closure",
        index=(("桶", "bucket"), ("组", "group")), default=None, default_note="0，不生效",
        unit="个", value_type="int", bounds=(0, 999), consumption="model", strength="hard",
        reconcile="reconcile_bucket",
        examples=("11月第二周必须修叶片", "窗口03 里高危至少修 3 个",
                  "这周风机003 的活全干完", "8号到10号必须开工两个齿轮箱"),
        note="按候选的开工时间落桶，口径写死在 README",
    ),
    "bucket_max": Slot(
        name="bucket_max", group="D", label="某时段内最多开工几个", route="closure",
        index=(("桶", "bucket"), ("组", "group")), default=None, default_note="不设上限",
        unit="个", value_type="int", bounds=(0, 999), consumption="model", strength="hard",
        reconcile="reconcile_bucket",
        examples=("窗口01 不修齿轮箱", "11月5号不安排新开工", "2号船周三别排",
                  "这周叶片最多修 1 个"),
    ),
    # E 组 · 时间界
    "earliest": Slot(
        name="earliest", group="E", label="最早何时开工", route="closure",
        index=(("组", "group"),), default=None, default_note="0，不生效",
        unit="时刻", value_type="minutes", consumption="model", strength="hard",
        reconcile="reconcile_earliest",
        examples=("任务018 最早 11月5号开工", "高危的也要等下周才能动"),
    ),
    "deadline_delta": Slot(
        name="deadline_delta", group="E", label="维修期限", route="data",
        index=(("组", "group"),), default=None, default_note="任务自带期限",
        unit="时刻", value_type="minutes", consumption="candidates", strength="hard",
        reconcile="reconcile_deadline",
        examples=("高危一周内干完", "齿轮箱提前到 20 号前完成", "这批活 18 号前必须收尾"),
        note="只走数据补丁，不走注入口：verify_schedule 按 task['截止分钟'] 复算，"
             "走注入口会出现两边口径不一致（R2）。"
             "且**只能提前不能放宽**——内核的期限补丁只做收紧，放宽会明确拒绝",
    ),
    # F 组 · 用哪条船
    "line_fixed": Slot(
        name="line_fixed", group="F", label="固定作业线", route="closure",
        index=(("任务", "task"),), default=None, default_note="自由选择作业线",
        value_type="int", bounds=(1, 100), consumption="model", strength="hard",
        reconcile="reconcile_line_fixed",
        examples=("任务018 只上 2 号船",),
    ),
    "cap_max": Slot(
        name="cap_max", group="F", label="同时占用的实例数上限", route="closure",
        index=(("维度", "cap_维度"), ("桶", "bucket")), default=None, default_note="跟随作业线数",
        unit="个", value_type="int", bounds=(0, 999), consumption="model", strength="hard",
        reconcile="reconcile_cap",
        examples=("11月8号最多开两条船", "这周每天最多动 3 台风机"),
        note="默认不是无穷大，是跟随 A 组作业线数——否则会把内核的 NoOverlap 语义改掉。"
             "桶必须是一个明确时段（某日/窗口）；**不带时段的「最多开 N 条船」是参数「最多作业线数」，不落这里**",
    ),
    # G 组 · 花多少
    "budget_max": Slot(
        name="budget_max", group="G", label="预算上限", route="limits_or_closure",
        index=(("口径", "budget_口径"), ("组", "group")), default=None, default_note="不设上限",
        unit="元或艘天", value_type="float", bounds=(0, 1e12), consumption="model",
        strength="hard", reconcile="reconcile_budget", optional=("组",),
        examples=("现金不超 80 万", "总成本压到 200 万内", "人工费单列不超 50 万",
                  "高危备件费不超 30 万", "租船最多 5 天"),
        note="现金/总成本走内核 limits；人工/备件/租船天走注入口",
    ),
    # H 组 · 先后同异（N 元）
    "before_seq": Slot(
        name="before_seq", group="H", label="先后顺序与间隔", route="closure",
        index=(("序列", "task_seq"),), default=None, default_note="不设顺序",
        unit="小时", value_type="float", bounds=(0, 720), consumption="model", strength="hard",
        reconcile="reconcile_before_seq",
        examples=("先修 018 再修 022", "按 018→022→025 的顺序做，中间隔 2 小时"),
        note="N 元是零新形态：链式展开成 N-1 条二元约束",
    ),
    "same_bucket": Slot(
        name="same_bucket", group="H", label="安排在同一天/同一窗口", route="closure",
        index=(("集合", "task_set"), ("粒度", "粒度")), default=None, default_note="不设共现",
        value_type="act", consumption="model", strength="hard",
        reconcile="reconcile_same_bucket", optional=("粒度",),
        examples=("018 和 022 同一天", "这三个放同一个窗口"),
        note="N 元同样是链式相等，零新形态",
    ),
    "all_diff": Slot(
        name="all_diff", group="H", label="分在不同天", route="closure",
        index=(("集合", "task_set"), ("粒度", "粒度")), default=None, default_note="不设互异",
        value_type="act", consumption="model", strength="hard",
        reconcile="reconcile_all_diff", optional=("粒度",),
        examples=("018 和 022 不许同一天", "这三台风机分在不同天"),
        note="新形态（CP-SAT AllDifferent），已过加族评审；二元时吃掉原来的 diff_bucket",
    ),
    "sev_before": Slot(
        name="sev_before", group="H", label="严重度层间硬顺序", route="closure",
        index=(("层对", "severity_pair"),), default=None, default_note="关",
        value_type="act", consumption="model", strength="hard",
        reconcile="reconcile_sev_before",
        examples=("高危没干完不许开中危",),
    ),
    "sev_gap": Slot(
        name="sev_gap", group="H", label="严重度层间最小间隔", route="closure",
        index=(("层对", "severity_pair"),), default=None, default_note="不设间隔",
        unit="小时", value_type="float", bounds=(0, 720), consumption="model", strength="hard",
        reconcile="reconcile_sev_gap",
        examples=("高危干完隔半天再开中危",),
    ),
    # I 组 · 只修哪些
    "scope_only": Slot(
        name="scope_only", group="I", label="只修这些", route="closure",
        index=(("范围", "group"),), default=None, default_note="全部",
        value_type="act", consumption="model", strength="hard",
        reconcile="reconcile_scope_only",
        examples=("这轮只修叶片", "只在窗口03 里干", "只修风机001", "只修这五个任务"),
        note="编译成「补集压零」。与 bucket_max 互补：话里有「只」用这个，有「不/别」用 bucket_max",
    ),
    # J 组 · 改数据
    "part_hours": Slot(
        name="part_hours", group="J", label="部件维修工时", route="data",
        index=(("部件", "part"), ("严重度", "severity"), ("海况", "sea")),
        default=None, default_note="部件库原值", unit="小时", value_type="float",
        bounds=(0.01, 1000), consumption="candidates", strength="hard",
        reconcile="reconcile_part_hours", optional=("严重度", "海况"),
        examples=("齿轮箱高危按 20 小时算", "恶劣海况工时翻倍"),
    ),
    "part_arrival": Slot(
        name="part_arrival", group="J", label="备件到货时间", route="data",
        index=(("部件", "part"),), default=None, default_note="库存流水原值",
        unit="时刻", value_type="minutes", consumption="candidates", strength="hard",
        reconcile="reconcile_part_arrival",
        examples=("叶片备件 12 号才能到",),
    ),
    "weather": Slot(
        name="weather", group="J", label="某日海况假设", route="timeline",
        index=(("日期", "day"), ("项", "项")), default=None, default_note="快照原值",
        value_type="float", consumption="candidates", strength="hard",
        reconcile="reconcile_weather", optional=("项",),
        examples=("11月8号风速按 15 米算", "那天浪高 2.5 米"),
    ),
    "hourly": Slot(
        name="hourly", group="J", label="某时刻电价/风速假设", route="timeline",
        index=(("时刻", "moment"), ("项", "项")), default=None, default_note="快照原值",
        value_type="float", consumption="candidates", strength="hard",
        reconcile="reconcile_hourly", optional=("项",),
        examples=("明天上午电价按 1.2 元算",),
    ),
    "window": Slot(
        name="window", group="J", label="窗口起止假设", route="timeline",
        index=(("窗口", "window"), ("端", "端")), default=None, default_note="快照原值",
        value_type="minutes", consumption="candidates", strength="hard",
        reconcile="reconcile_window", optional=("端",),
        examples=("窗口03 推迟两天开始",),
    ),
    # A 组里唯一不是系数的：部分排程开关
    "allow_partial": Slot(
        name="allow_partial", group="A", label="允许少修几个", route="partial",
        default=False, default_note="否（完整排程）", value_type="bool",
        consumption="model", strength="hard", reconcile="reconcile_partial",
        examples=("排不下就少修几个吧",),
        note="也可由 count_max/scope_only 隐式触发，判据只有一份（apply.needs_partial）",
    ),
}


def audit_registry() -> list[str]:
    """注册表自检：登记项不全的槽位直接报出来，不许带病上线。

    对账函数为空是最严重的一类——它会让「加了族却没对账」这个大一修的窟窿重新出现。
    """
    errors: list[str] = []
    for name, slot in SLOTS.items():
        if not slot.label:
            errors.append(f"{name}: 缺人话名")
        if not slot.route:
            errors.append(f"{name}: 缺落点")
        if not slot.reconcile:
            errors.append(f"{name}: 缺对账函数（注册即承诺，不许空）")
        if not slot.default_note:
            errors.append(f"{name}: 缺默认值说明（默认必须等于「不生效」）")
        if not slot.examples:
            errors.append(f"{name}: 缺业务话术例子（提示词与回归集都靠它）")
        for index_name, kind in slot.index:
            if kind not in INDEX_KINDS and kind not in ("severity", "sea"):
                errors.append(f"{name}: 索引 {index_name} 的种类 {kind} 未登记")
    for alias, system_name in PARAM_ALIASES.items():
        if alias not in PARAM_SLOTS:
            errors.append(f"参数 {alias}→{system_name} 没有单位/界/消费点登记")
        elif PARAM_SLOTS[alias].get("消费点") not in ("model", "candidates"):
            errors.append(f"参数 {alias} 的消费点未登记")
    return errors


def slots_by_route(route: str) -> list[Slot]:
    return [s for s in SLOTS.values() if s.route == route or
            (route == "limits" and s.route == "limits_or_closure") or
            (route == "closure" and s.route == "limits_or_closure")]


def slot_names() -> list[str]:
    return sorted(SLOTS) + sorted(PARAM_SLOTS)


# ── 索引解析 ────────────────────────────────────────────────────────────
# 组与桶最终都是「作用在候选行上的谓词」——这是全部族共用的唯一抽象。
# 候选行字段（product_planner.candidates + build_model 补的作业线）：
#   任务编号、风机编号、部件编号、部件名称、严重等级、窗口编号、开始、完成、作业线

def _resolve_entities():
    """延迟导入 dsl_core 的实体解析器（中立项，不引入大一修的编译器）。"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from dsl_core.registry import entities  # noqa: PLC0415
    return entities


def group_predicate(data, spec: dict[str, Any]) -> tuple[Callable[[dict], bool] | None, str | None]:
    """把「组」索引解析成候选行谓词。返回 (谓词, 错误)；错误时谓词为 None。

    精确匹配，不做模糊匹配（沿用 part_catalog 的纪律）：「叶片」不许顺手匹配掉「叶片备件」。
    """
    entities = _resolve_entities()
    kind = spec.get("kind")
    value = spec.get("value")
    if kind == "全部" or kind is None:
        return (lambda c: True), None
    if kind == "严重度":
        if value not in ("高", "中", "低"):
            return None, f"严重度只能是 高/中/低，收到 {value!r}"
        return (lambda c, v=value: c["严重等级"] == v), None
    if kind == "部件":
        pid = entities.resolve_part(data, str(value))
        if pid is None:
            return None, f"部件 {value!r} 不在部件库里"
        return (lambda c, p=pid: c["部件编号"] == p), None
    if kind == "风机":
        tid = entities.resolve_turbine(data, str(value))
        if tid is None:
            return None, f"风机 {value!r} 不在告警快照里"
        return (lambda c, t=tid: c["风机编号"] == t), None
    if kind == "任务":
        tid = entities.resolve_task(data, str(value))
        if tid is None:
            return None, f"任务 {value!r} 不在告警快照里"
        return (lambda c, t=tid: c["任务编号"] == t), None
    if kind == "任务清单":  # 开放：任意组合，只能事后查
        if not isinstance(value, (list, tuple)) or len(value) < 1:
            return None, f"任务清单至少要给一个任务编号，收到 {value!r}"
        ids = []
        for item in value:
            tid = entities.resolve_task(data, str(item))
            if tid is None:
                return None, f"任务 {item!r} 不在告警快照里"
            ids.append(tid)
        return (lambda c: c["任务编号"] in ids), None
    if kind == "作业线":
        try:
            line = int(value)
        except (TypeError, ValueError):
            return None, f"作业线必须是数字，收到 {value!r}"
        if not 1 <= line <= min(data.params["最多作业线数"], data.params["人员总上限"]):
            return None, f"作业线 {line} 超出当前可用线数"
        return (lambda c, ln=line: c["作业线"] == ln), None
    return None, f"未知的组类别 {kind!r}，可填：{'、'.join(GROUP_KINDS)}"


def _resolve_window(data, ref: str) -> str | None:
    """窗口按 ``data.windows`` 匹配，不用 entities.resolve_window。

    后者查的是 ``observation_windows``——那里面还留着已过规划边界、被跳过的窗口
    （本次快照里窗口01/02 就是 STARTS_BEFORE_PLANNING_BOUNDARY）。候选行只可能落在
    ``data.windows`` 里，按观察窗口解析会让「窗口01 必须修几个」通过校验、
    却在注入口里匹配到零个候选，静默变成一条恒真的空约束。
    """
    ref = str(ref).strip()
    ids = {w["编号"] for w in data.windows}
    if ref in ids:
        return ref
    plain = ref.lstrip("窗口").zfill(2)
    return f"窗口{plain}" if f"窗口{plain}" in ids else None


def bucket_predicate(data, spec: dict[str, Any]) -> tuple[Callable[[dict], bool] | None, str | None]:
    """把「桶」索引解析成候选行谓词，按**开工时间**落桶（口径写死）。"""
    entities = _resolve_entities()
    kind = spec.get("kind")
    value = spec.get("value")
    if kind is None or kind == "全程":
        return (lambda c: True), None
    if kind == "窗口":
        wid = _resolve_window(data, value)
        if wid is None:
            ids = "、".join(w["编号"] for w in data.windows)
            return None, f"窗口 {value!r} 不在本次规划范围内，当前可用：{ids}"
        return (lambda c, w=wid: c["窗口编号"] == w), None
    if kind == "某日":
        day = entities.resolve_day(data, str(value))
        if day is None:
            return None, f"日期 {value!r} 不在规划期内"
        return (lambda c, d=day: c["开始"] // 1440 == d), None
    if kind == "日期区间":  # 开放：任意起止，只能事后查
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None, f"日期区间要给起止两个值，收到 {value!r}"
        lo = entities.resolve_day(data, str(value[0]))
        hi = entities.resolve_day(data, str(value[1]))
        if lo is None or hi is None:
            return None, f"日期区间 {value!r} 有端点不在规划期内"
        if lo > hi:
            return None, f"日期区间起点 {value[0]} 晚于终点 {value[1]}"
        return (lambda c: lo <= c["开始"] // 1440 <= hi), None
    return None, f"未知的桶类别 {kind!r}，可填：{'、'.join(BUCKET_KINDS)}"


def group_task_ids(data, spec: dict[str, Any]) -> list[str] | None:
    """组索引对应的在册任务编号（静态预检用：这格里到底有几个任务）。"""
    entities = _resolve_entities()
    kind = spec.get("kind")
    value = spec.get("value")
    if kind in (None, "全部"):
        return [t["任务编号"] for t in data.tasks]
    if kind == "严重度":
        return [t["任务编号"] for t in data.tasks if t["严重等级"] == value]
    if kind == "部件":
        pid = entities.resolve_part(data, str(value))
        return [t["任务编号"] for t in data.tasks if t["部件编号"] == pid] if pid else None
    if kind == "风机":
        tid = entities.resolve_turbine(data, str(value))
        return [t["任务编号"] for t in data.tasks if t["风机编号"] == tid] if tid else None
    if kind == "任务":
        tid = entities.resolve_task(data, str(value))
        return [tid] if tid else None
    if kind == "任务清单":
        ids = [entities.resolve_task(data, str(v)) for v in value]
        return [i for i in ids if i] or None
    if kind == "作业线":
        return [t["任务编号"] for t in data.tasks]  # 作业线是求解时才定的，按全部算
    return None
