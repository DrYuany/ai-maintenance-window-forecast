"""读请求与执行侧：能答的答准，答不上的说答不上来，超时不许说成不可行。

这三件事有一个共同点：**它们都会产出一个数，而数会被拿去做决定。**
所以测试的重点不是"函数返回成功"，是"口径对不对、错了会不会说错"。
"""
from __future__ import annotations

import answer
import execute


# ── 1. 问的是哪个参数 ──────────────────────────────────────────────────────

def test_alias_hits_the_right_param():
    assert answer._match_params("船租现在多少") == ["船舶日租金"]
    assert answer._match_params("每天能干几小时") == ["每日有效维修上限"]
    assert answer._match_params("能用几条作业线") == ["最大作业线"]


def test_generic_word_answers_both():
    """"风速上限"对应常态和安全两个参数，机器不许替用户挑一个。"""
    assert answer._match_params("风速上限是多少") == ["常态风速上限", "安全风速上限"]
    # 说清楚了就只答一个
    assert answer._match_params("安全风速上限是多少") == ["安全风速上限"]


def test_unknown_question_says_so_instead_of_guessing():
    reading = answer.answer("今天天气怎么样", "fact")
    assert not reading.ok
    assert reading.error == "no_param"
    assert any("能直接查的有" in line for line in reading.lines)


def test_fact_returns_value_and_source():
    reading = answer.answer("船租现在多少", "fact")
    assert reading.ok
    item = reading.data["params"][0]
    assert item["param"] == "船舶日租金"
    assert item["value"] is not None
    assert "参数快照" in item["source"], item["source"]


# ── 2. 数值与时间的显示 ────────────────────────────────────────────────────

def test_float_integers_print_without_decimals():
    """`12000.0 元` 会让人以为是个估算值。整数就写整数。"""
    assert answer._fmt(12000.0) == "12000"
    assert answer._fmt(10.5) == "10.5"


def test_iso_time_is_shortened():
    assert answer._when("2026-11-18T18:00:00+08:00") == "11月18日 18:00"
    assert answer._when(None) == "—"


def test_money_switches_unit():
    assert answer._money(12000) == "1.2 万元"
    assert answer._money(800) == "800 元"


# ── 3. 问的是哪两个策略 ────────────────────────────────────────────────────

def test_strategy_aliases():
    assert answer._strategies_of("经济优先和风险优先差多少") == ["成本最低优先", "高风险优先抢修"]
    assert answer._strategies_of("只看成本和最短完成") == ["成本最低优先", "最短完成优先"]


def test_one_strategy_gets_a_default_rival():
    """只说了一个策略也要能比——拿默认对手，并在口径里写清是哪一个。"""
    pair = answer._strategies_of("高风险优先会怎样")
    assert pair[0] == "高风险优先抢修" and len(pair) == 2


# ── 4. 执行侧：超时与不可行必须分开 ────────────────────────────────────────

def test_empty_dsl_is_not_sent_to_solver():
    result = execute.solve({})
    assert not result.ok and result.status == "EMPTY"
    assert "没有可执行" in result.error


def test_timeout_is_reported_as_timeout(monkeypatch):
    """超时 ≠ 不可行。这句话在业务上的差别是"再等等"和"改需求"。"""
    import subprocess

    def boom(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=1)

    monkeypatch.setattr(execute.subprocess, "run", boom)
    result = execute.solve({"parameters": [{"field": "船舶日租金", "op": "=", "value": 8000}]},
                           seconds=1, timeout=2)
    assert result.status == "TIMEOUT"
    assert "不说明不可行" in result.error


def test_worker_crash_reports_stderr(monkeypatch):
    class Fake:
        returncode = 1
        stdout = ""
        stderr = "Traceback...\nModuleNotFoundError: No module named 'ortools'\n"

    monkeypatch.setattr(execute.subprocess, "run", lambda *a, **k: Fake())
    result = execute.solve({"parameters": [{"field": "船舶日租金", "op": "=", "value": 8000}]})
    assert result.status == "ERROR"
    assert "ortools" in result.error


def test_bad_json_from_worker_is_not_swallowed(monkeypatch):
    class Fake:
        returncode = 0
        stdout = "这不是 JSON"
        stderr = ""

    monkeypatch.setattr(execute.subprocess, "run", lambda *a, **k: Fake())
    result = execute.solve({"parameters": [{"field": "船舶日租金", "op": "=", "value": 8000}]})
    assert result.status == "BAD_OUTPUT"


def test_execution_payload_shape():
    result = execute.Execution(ok=True, status="FEASIBLE", seconds=1.5,
                               payload={"scheduled": 3}, mode="solve")
    packed = result.as_dict()
    assert packed["status"] == "FEASIBLE" and packed["mode"] == "solve"
    assert packed["payload"]["scheduled"] == 3
