"""评测集：分层，每层一个"要证明什么"。

为什么不用 backend 那 221 条当主集：它们是**规则主导版**的评测集，标签是按规则层能力划的
（很多 dsl 标签的句子，两边的正确读法都可能是 clarify）。拿它当主集会得到一堆
"谁也没错"的争议项，跑完不知道该改哪。

所以分两套：

- `SUITE`（本文件手写的 67 条）：每条都写清"这一条在证明什么"，
  断言只用机器能判的东西——route、有没有退回、有没有丢数、DSL 里有没有那个参数/原语。
- `backend_cases()`（`--suite backend`）：把 221 条按原标签跑一遍当压力测试，
  只看总体分布，不逐条判对错。

标签口径（和 A1~A12 对齐）：

| 字段 | 意思 |
|---|---|
| `route` / `route_in` | 最终动作必须落在这里（`route_in` 是多选一） |
| `params` | `dsl.parameters` 里必须出现这些 `field` |
| `constraints` | `dsl.constraints` 里必须出现这些 `type` |
| `objectives` | `dsl.objectives` 里必须出现这些 `type` |
| `data` | `dsl.data` 里必须出现这些 `kind` |
| `unassigned` | 必须至少有一条 unassigned |
| `forbid_params` | **route=dsl 时**，`dsl.parameters` 里不许出现这些 field（把一天的事写成全程参数） |
| `forbid_constraints` | **route=dsl 时**，`dsl.constraints` 里不许出现这些 type（把数据变更伪装成约束） |

「不许出现」只在**真要执行**的时候查。route=clarify 时草稿不会被编译，
里面留着一次失败尝试没有害处——这时候判"碰了不该碰的"，
等于把"改错了主意"当成"做错了事"。
| `clean` | 这条不许被验收退回（默认 True，除了刻意构造的对抗项） |
| `note` | 为什么这么标 |
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_CASES = ROOT / "backend" / "eval_cases" / "dsl_questions.jsonl"

SUITE: tuple[dict, ...] = (
    # ── A 单句改参数：最基础，错了别的都不用看 ─────────────────────────────
    {"id": "A01", "tag": "单句·参数", "text": "船租改成5万",
     "route": "dsl", "params": ["船舶日租金"]},
    {"id": "A02", "tag": "单句·参数", "text": "船租上涨20%",
     "route": "dsl", "params": ["船舶日租金"], "note": "相对量，op 应该是 *"},
    {"id": "A03", "tag": "单句·参数", "text": "人工单价改成300元",
     "route": "dsl", "params": ["维修人工单价"]},
    {"id": "A04", "tag": "单句·参数", "text": "每天最多8小时",
     "route": "dsl", "params": ["每日有效维修上限"]},
    {"id": "A05", "tag": "单句·参数", "text": "最多两条作业线",
     "route": "dsl", "params": ["最大作业线"]},
    {"id": "A06", "tag": "单句·参数", "text": "人员上限改成20人",
     "route": "dsl", "params": ["可调动人员总数"]},
    {"id": "A07", "tag": "单句·参数", "text": "每天8点开工",
     "route": "dsl", "params": ["每天开始维修"]},
    {"id": "A08", "tag": "单句·参数", "text": "风机之间的转移时间改成1小时",
     "route": "dsl", "params": ["风机间转移"]},
    {"id": "A09", "tag": "单句·参数", "text": "人手砍一半",
     "route": "dsl", "params": ["可调动人员总数"], "note": "说法式相对量，op 应该是 *"},
    {"id": "A10", "tag": "单句·参数", "text": "少干点，每天6小时",
     "route": "dsl", "params": ["每日有效维修上限"]},

    # ── B 复合句：规则层的历史死穴（只认前半句） ───────────────────────────
    {"id": "B01", "tag": "复合句", "text": "把船租降到8万，每天最多干10小时",
     "route": "dsl", "params": ["船舶日租金", "每日有效维修上限"]},
    {"id": "B02", "tag": "复合句", "text": "人工单价涨到200，任务003最晚11月9日修完",
     "route": "dsl", "params": ["维修人工单价"], "constraints": ["deadline"]},
    {"id": "B03", "tag": "复合句", "text": "任务001和任务002不要同一天做，钱不超过300万",
     "route": "dsl", "constraints": ["co_occur", "budget"]},
    {"id": "B04", "tag": "复合句", "text": "船租涨两成，人工也涨两成",
     "route": "dsl", "params": ["船舶日租金", "维修人工单价"]},
    {"id": "B05", "tag": "复合句", "text": "每天只能干8小时，别排任务005",
     "route": "dsl", "params": ["每日有效维修上限"], "constraints": ["exclude"]},
    {"id": "B06", "tag": "复合句", "text": "先修任务001再修任务002，钱别超300万",
     "route": "dsl", "constraints": ["precedence", "budget"]},
    {"id": "B07", "tag": "复合句", "text": "最多两条线，每天8小时，预算300万",
     "route": "dsl", "params": ["最大作业线", "每日有效维修上限"], "constraints": ["budget"]},
    {"id": "B08", "tag": "复合句", "text": "任务018不能晚于11月8日，并且11月3日到5日别开工",
     "route": "dsl", "constraints": ["deadline", "forbidden_dates"]},

    # ── C 约束与目标：表 A 里没有，只能靠积木 ─────────────────────────────
    {"id": "C01", "tag": "积木·约束", "text": "任务001和任务002必须同一天做",
     "route": "dsl", "constraints": ["co_occur"]},
    {"id": "C02", "tag": "积木·约束", "text": "先修任务001，隔两天再修任务002",
     "route": "dsl", "constraints": ["precedence"]},
    {"id": "C03", "tag": "积木·约束", "text": "至少要修完5个",
     "route": "dsl", "constraints": ["cardinality"]},
    {"id": "C04", "tag": "积木·约束", "text": "11月3日到5日别开工",
     "route": "dsl", "constraints": ["forbidden_dates"]},
    {"id": "C05", "tag": "积木·约束", "text": "把任务001固定到2号船",
     "route": "dsl", "constraints": ["fix"]},
    {"id": "C06", "tag": "积木·目标", "text": "整体越早收工越好",
     "route": "dsl", "objectives": ["minimize"]},
    {"id": "C07", "tag": "积木·目标", "text": "总成本压到最低",
     "route": "dsl", "objectives": ["minimize"]},
    {"id": "C08", "tag": "积木·约束", "text": "钱不超过300万",
     "route": "dsl", "constraints": ["budget"]},

    # ── D 数据补丁：改的不是参数，是那天的输入 ─────────────────────────────
    {"id": "D01", "tag": "数据补丁", "text": "11月5日风15米每秒",
     "route": "dsl", "data": ["weather"]},
    {"id": "D02", "tag": "数据补丁", "text": "明天风大，先别出海",
     "route": "dsl", "data": ["weather"], "note": "要基准日期才落得了地"},
    {"id": "D03", "tag": "数据补丁", "text": "叶片11月5日到2套，到货前不能开工",
     "route": "dsl", "data": ["part_arrival"]},

    # ── E 不改：问句与闲聊。这类最怕"手滑改了" ─────────────────────────────
    {"id": "E01", "tag": "不改", "text": "你好", "route": "not_change"},
    {"id": "E02", "tag": "不改", "text": "现在船租多少", "route": "not_change"},
    {"id": "E03", "tag": "不改", "text": "为什么这么贵", "route": "not_change"},
    {"id": "E04", "tag": "不改", "text": "解释一下这个方案", "route": "not_change"},
    {"id": "E05", "tag": "不改", "text": "谢谢", "route": "not_change"},
    {"id": "E06", "tag": "不改", "text": "每天能干几小时",
     "route": "not_change", "note": "问现状，不是改"},

    # ── F 能力缺口：做不到要说做不到，不许拿近似顶 ─────────────────────────
    {"id": "F01", "tag": "缺口·整句外", "text": "碳排放最小化", "route": "reject"},
    {"id": "F02", "tag": "缺口·整句外", "text": "改一下叶片的颜色", "route": "reject"},
    {"id": "F03", "tag": "缺口·整句外", "text": "把风险降低50%", "route": "reject"},
    {"id": "F04", "tag": "缺口·一半能做", "text": "成本压到最低，但高风险的必须全修",
     "route": "dsl", "unassigned": True, "note": "前半能做，后半没有「全部」"},
    {"id": "F05", "tag": "缺口·范围", "text": "只排风机003的任务",
     "route_in": {"reject", "clarify"}, "note": "没有筛整份计划的原语"},
    {"id": "F06", "tag": "缺口·量目录", "text": "船租和人工加起来不超过300万",
     "route_in": {"dsl", "clarify", "reject"}, "unassigned": None,
     "note": "量目录里没有船租单项，只要不编一个假量就行"},

    # ── I 危险改动：做错比不做更糟，这一组专测"忍住不动" ─────────────────────
    {"id": "I01", "tag": "危险·按天限制", "text": "明天只有3个人能出海",
     "route_in": {"clarify", "reject"}, "forbid_params": ["可调动人员总数"],
     "note": "表 A 的人员是全程参数；写成 3 会把一天的事变成整月的事"},
    {"id": "I02", "tag": "危险·删除", "text": "删掉任务012",
     "route_in": {"clarify", "reject"}, "forbid_constraints": ["exclude"],
     "note": "exclude 是「这次不修」，不是「删掉」"},
    {"id": "I03", "tag": "危险·批量", "text": "算两个场景：船租涨20%和最多两条线，对比一下",
     "route_in": {"clarify"},
     "note": "一次只处理一个场景；只做一半比不做更糟，因为用户以为两个都算了"},
    {"id": "I04", "tag": "危险·改输入", "text": "把天气改成晴天",
     "route_in": {"clarify", "reject"},
     "note": "天气是输入数据，不是排程约束"},
    {"id": "I05", "tag": "危险·加权", "text": "高严重完成数占六成，总经济成本占四成",
     "route_in": {"clarify", "reject", "dsl"}, "unassigned": True,
     "note": "表 D 没有加权；要权重只能明说做不到"},

    # ── G 方向：唯一一类"全过还更糟" ───────────────────────────────────────
    {"id": "G01", "tag": "方向", "text": "预算至少要留300万",
     "route": "dsl", "constraints": ["budget"]},
    {"id": "G02", "tag": "方向", "text": "每天不能超过8小时",
     "route": "dsl", "params": ["每日有效维修上限"]},
    {"id": "G03", "tag": "方向", "text": "任务003不能早于11月9日",
     "route": "dsl", "constraints": ["earliest"]},
    {"id": "G04", "tag": "方向", "text": "完工时间不能晚于11月20日",
     "route_in": {"clarify", "reject"},
     "note": "整体完工期限没有对应的积木：deadline 只能指着某个任务，量目录里也没有能做日期上界的量。"
             "（原先标成 dsl+deadline 是错的——那等于要求模型捏造一个 task）"},

    # ── H 时间与量级：说错的代价最直接 ─────────────────────────────────────
    {"id": "H01", "tag": "时间", "text": "明天的风浪太大，别出海",
     "route": "dsl", "data": ["weather"]},
    {"id": "H02", "tag": "时间", "text": "月底之前把任务018修完",
     "route_in": {"clarify", "dsl"}, "note": "「月底」有歧义：说清就落表，说不清就该问"},
    {"id": "H03", "tag": "量级", "text": "船租涨到12万",
     "route": "dsl", "params": ["船舶日租金"]},
    {"id": "H04", "tag": "量级", "text": "预算砍到300万",
     "route": "dsl", "constraints": ["budget"]},

    # ── J 回归：跑压力集时真踩到的坑，一条一个坑 ────────────────────────────
    # 这一组不看"效果好不好"，只看"同一个坑会不会踩第二次"。
    # 每条都注明修之前是什么样，方便以后判断是不是又退回去了。
    {"id": "J01", "tag": "回归·相对量", "text": "船少一条看看",
     "route": "dsl", "params": ["最大作业线"],
     "note": "修前：反问「少到几条」，因为拿不准「船」指什么；现值表里就有 3 条"},
    {"id": "J02", "tag": "回归·策略", "text": "先修严重的",
     "route": "dsl", "objectives": ["strategy"],
     "note": "修前：反问「是哪个任务先」；没点名就是整体优先级"},
    {"id": "J03", "tag": "回归·偷换参数", "text": "同一条船干完一个活要歇2小时",
     "route_in": {"reject", "clarify"}, "forbid_params": ["风机间转移"],
     "note": "修前：写成 风机间转移=2。那是换风机的路上要多久，不是活儿之间空多久"},
    {"id": "J04", "tag": "回归·判据误伤", "text": "T01和T02不能同时修",
     "route": "dsl", "constraints": ["co_occur"],
     "note": "修前：DSL 完全正确，但 A1 因为切段剥了「同时」判成没覆盖"},
    {"id": "J05", "tag": "回归·判据误伤", "text": "先最小化最后完工时间，再最小化总经济成本",
     "route": "dsl", "objectives": ["lexmin"],
     "note": "修前：A6 把 lexmin 的两个阶段当成「同一行两个值」"},
    {"id": "J06", "tag": "回归·倍率落表", "text": "齿轮箱修起来更费时了，加20%",
     "route": "dsl", "data": ["part_hours"],
     "note": "部件工时倍率走数据补丁，不是改参数"},
    {"id": "J07", "tag": "回归·相对日期", "text": "窗口02提前两天开始",
     "route": "dsl", "data": ["window"],
     "note": "「提前两天」要按基准日换算成具体时刻"},
    {"id": "J08", "tag": "回归·目标", "text": "尽量让大家都早点干完",
     "route": "dsl", "objectives": ["minimize"],
     "note": "「大家都早点」是完工时间和，不是最后完工时间"},
    {"id": "J09", "tag": "回归·改参数", "text": "船坏了，只剩两条作业线",
     "route": "dsl", "params": ["最大作业线"],
     "note": "事件句也要落到参数上，不能因为带了个「坏了」就当做不到"},
    {"id": "J10", "tag": "回归·模糊诉求", "text": "尽量安排得合理一些",
     "route_in": {"clarify"},
     "note": "修前：not_change。说不出要改什么不等于「现状就合理」"},
    {"id": "J11", "tag": "回归·能力缺口", "text": "严重故障11月8日前必须修完",
     "route_in": {"clarify", "reject"}, "forbid_constraints": ["deadline"],
     "note": "按严重级别批量截止要「全部」，没有这块积木；单独给一个任务写 deadline 是错的"},
    {"id": "J12", "tag": "回归·口语日期", "text": "3号那天不要动工",
     "route": "dsl", "constraints": ["forbidden_dates"],
     "note": "修前：反问「是11月还是12月」。只说几号就取基准之后最近的那一个"},
    {"id": "J13", "tag": "回归·口语日期", "text": "把4号和5号空出来",
     "route": "dsl", "constraints": ["forbidden_dates"],
     "note": "一次说几个号也一样；不要因为有两个日期就去问月份"},
)


def backend_cases() -> list[dict]:
    """backend 的 221 条，按原标签当压力集。

    只做一件映射：`batch` 在本产品里没有对应原语，正确行为是**不要静默只做一半**，
    所以接受 clarify / not_change / reject 三种"没有自作主张"的结果。
    """
    if not BACKEND_CASES.exists():
        return []
    out: list[dict] = []
    for line in BACKEND_CASES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        route = row.get("route")
        case = {"id": row["id"], "tag": f"backend·{row.get('intent')}",
                "text": row["utterance"], "source_route": route}
        if route == "dsl":
            case["route"] = "dsl"
        elif route in ("not_change", "clarify", "reject"):
            case["route"] = route
        elif route == "batch":
            case["route_in"] = {"clarify", "not_change", "reject"}
            case["note"] = "批量场景没有对应原语，不许只做一半"
        else:                                   # dsl_or_clarify / accept_any
            case["route_in"] = {"dsl", "clarify", "not_change", "reject"}
        out.append(case)
    return out


def suite(name: str = "core") -> list[dict]:
    if name == "core":
        return [dict(case) for case in SUITE]
    if name == "backend":
        return backend_cases()
    return [dict(case) for case in SUITE] + backend_cases()
