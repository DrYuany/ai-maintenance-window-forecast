"""参数注册表：读参数总表.csv（122 项），登记消费点与可改性。

消费点是路由判据的唯一事实来源：改的输入被 candidates()（生成层/时间维度）
还是 build_model()（选择层/成本组合维度）消费。本表是 §6.3 四类重归的机器可读版。

注意：这里只覆盖"系统参数表"（122 项）。部件库/告警任务/库存/时序字段
是另一类"输入数据"，原型阶段暂以 data_patches 机制为准（第二期统一）。
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

_CSV_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "模型参数" / "参数总表.csv"
)

# 生成层参数（改了必须重新生成候选）——来源：设计文档 §6.3 的 ✅生成层 列
GENERATION_LAYER: frozenset[str] = frozenset({
    "候选开工粒度",
    "每天开始维修", "每天结束维修", "每日有效维修上限",
    "风机间转移", "允许夜间转移", "允许跨天暂停",
    "常态风速上限", "常态浪高上限", "安全风速上限", "安全浪高上限", "天气安全缓冲",
    "高严重度截止窗口序号", "中严重度截止窗口序号", "低严重度截止窗口序号",
    "补库延期量",
})

# 选择层参数（改了复用候选，只重建模型）
SELECTION_LAYER: frozenset[str] = frozenset({
    "候选风机", "候选部件",
    "最大作业线", "可调动人员总数",
    "船舶日租金", "维修人工单价", "每任务耗材附加费", "船租是否包含燃油",
    "故障等待损失比例", "检修期间停机比例", "潜在功率整体倍率", "电价整体倍率",
    "正式目标", "自动允许超期", "未知期限任务是否可提前修",
    "发电损失计入总经济成本", "船租计入总经济成本", "人工计入总经济成本", "维修包计入总经济成本",
    "标准目标策略", "共同最低完成任务数",
    "审批预计等待", "真实预测过期阈值",
    "总求解预算", "允许相对差距", "最多对比方案数",
    "预算限制启用", "费用预算上限", "预算覆盖口径",
    "强制维修任务", "禁止新开工风机", "成本敏感性倍率列表",
    "指定任务截止时间", "固定使用的批准窗口", "禁止新增开工日期",
})


@lru_cache(maxsize=1)
def _rows() -> tuple[dict[str, Any], ...]:
    with _CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        return tuple(csv.DictReader(f))


def count() -> int:
    return len(_rows())


def lookup(name: str) -> dict[str, Any] | None:
    for row in _rows():
        if row["参数名称"] == name:
            return dict(row)
    return None


def consumption_point(name: str) -> str:
    """消费点查表：candidates / build_model / unknown。

    这是路由判据的唯一入口：改动含 candidates 消费的参数 → 重新生成候选。
    """
    if name in GENERATION_LAYER:
        return "candidates"
    if name in SELECTION_LAYER:
        return "build_model"
    if lookup(name) is not None:
        return "build_model"  # 默认落在选择层；待扩展/固定的由"能否改"另行把关
    return "unknown"


def is_changeable(name: str) -> bool:
    """能否改（✅ 两类可改；🚧待扩展/🔒固定 返回 False）。"""
    if name in GENERATION_LAYER or name in SELECTION_LAYER:
        return True
    return False
