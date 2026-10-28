from __future__ import annotations

from app.core.version_compare import compare_versions, render_compare_summary
from app.schemas.agent import CandidateWindow, RepairScheduleItem, WindowVersion


def _version(
    version_id: str,
    *,
    tasks: list[str],
    total_cost: float,
    input_hash: str = "hash-a",
    generated_at: str = "2026-10-27T10:00:00+08:00",
    unscheduled: list[dict] | None = None,
) -> WindowVersion:
    return WindowVersion(
        id=version_id,
        conversationId="conv_test",
        generatedAt=generated_at,
        userPrompt="测试方案",
        focusTag="成本最低优先",
        intentType="generate_version",
        parameterSnapshot={},
        resultSummary={
            "strategy": "成本最低优先",
            "objectiveMode": "分层",
            "topWindowId": "窗口02",
            "totalTaskCount": 5,
            "scheduledTaskCount": len(tasks),
            "totalCost": total_cost,
            "cashCost": 100_000,
            "generationLoss": total_cost - 100_000,
            "latestCompletion": "2026-11-10T18:00:00+08:00",
            "vesselDays": 26,
            "verified": True,
            "baselineSavings": {"严重性优先": None, "低风速优先": 0.0},
            "unscheduledTasks": unscheduled or [{"任务编号": "任务003", "原因": "竞争未入选"}],
            "solver": {
                "解状态": "FEASIBLE",
                "终止原因": "TIME_LIMIT",
                "总耗时秒": 300.0,
                "求解器参数": {"num_search_workers": 1},
            },
            "data": {"输入哈希": input_hash, "规划时点": "2026-10-23T18:00:00+08:00"},
        },
        repairSchedule=[
            RepairScheduleItem(
                taskId=task,
                turbineId=index + 1,
                componentName="叶片",
                startHour=100 + index,
                endHour=104 + index,
                durationHours=4,
                startTime=f"2026-11-02 {8 + index}:00",
                endTime=f"2026-11-02 {12 + index}:00",
            )
            for index, task in enumerate(tasks)
        ],
        windows=[
            CandidateWindow(
                id=f"cand_{version_id}",
                windowId="窗口02",
                versionId=version_id,
                conversationId="conv_test",
                generatedAt=generated_at,
                focusTag="成本最低优先",
                generatedFromPrompt="测试",
                displayOrder=1,
                metrics={
                    "duration": 240,
                    "opportunityCost": 3_000_000,
                    "directCost": 100_000,
                    "riskLevel": "high",
                    "startTime": "2026-11-02 00:00",
                    "endTime": "2026-11-12 00:00",
                },
            )
        ],
        modelFingerprint=f"fp-{version_id}",
    )


def test_compare_reports_metrics_and_task_diff() -> None:
    left = _version("ver_left0001", tasks=["任务001", "任务002"], total_cost=5_110_000)
    right = _version(
        "ver_right001",
        tasks=["任务001", "任务004"],
        total_cost=4_720_000,
        unscheduled=[{"任务编号": "任务002", "原因": "竞争未入选"}],
    )

    result = compare_versions(left, right, left_number=1, right_number=2)

    metrics = {item["key"]: item for item in result["layer1Facts"]["metrics"]}
    assert metrics["totalCost"]["delta"] == -39.0
    assert metrics["totalCost"]["winner"] == "right"
    assert metrics["scheduledTasks"]["delta"] == 0

    task_set = result["layer2Drivers"]["taskSet"]
    assert [item["taskId"] for item in task_set["onlyLeft"]] == ["任务002"]
    assert [item["taskId"] for item in task_set["onlyRight"]] == ["任务004"]
    assert task_set["onlyLeft"][0]["otherReason"] == "竞争未入选"


def test_compare_blocks_savings_claim_when_task_sets_differ() -> None:
    left = _version("ver_left0001", tasks=["任务001"], total_cost=5_000_000)
    right = _version("ver_right001", tasks=["任务001", "任务002"], total_cost=4_000_000)

    result = compare_versions(left, right, left_number=1, right_number=2)

    comparability = result["layer1Facts"]["comparability"]
    assert comparability["costComparable"] is True
    assert comparability["savingsClaimAllowed"] is False
    assert set(result["layer1Facts"]["quickVerdict"].values()) <= {"left", "right", "tie", None}


def test_compare_allows_savings_when_snapshot_and_tasks_match() -> None:
    left = _version("ver_left0001", tasks=["任务001", "任务002"], total_cost=5_000_000)
    right = _version("ver_right001", tasks=["任务001", "任务002"], total_cost=4_000_000)

    result = compare_versions(left, right, left_number=1, right_number=2)

    assert result["layer1Facts"]["comparability"]["savingsClaimAllowed"] is True
    assert result["layer1Facts"]["quickVerdict"]["cheaper"] == "right"


def test_compare_marks_different_snapshots_as_not_comparable() -> None:
    left = _version("ver_left0001", tasks=["任务001"], total_cost=5_000_000, input_hash="a")
    right = _version("ver_right001", tasks=["任务001"], total_cost=4_000_000, input_hash="b")

    result = compare_versions(left, right, left_number=1, right_number=2)

    assert result["layer1Facts"]["comparability"]["sameDataSnapshot"] is False
    assert result["layer1Facts"]["comparability"]["costComparable"] is False
    assert result["layer1Facts"]["quickVerdict"]["cheaper"] is None


def test_compare_is_deterministic_for_same_pair() -> None:
    left = _version("ver_left0001", tasks=["任务001"], total_cost=5_000_000)
    right = _version("ver_right001", tasks=["任务001"], total_cost=4_000_000)

    first = compare_versions(left, right, left_number=1, right_number=2)
    second = compare_versions(left, right, left_number=1, right_number=2)

    assert first["comparisonId"] == second["comparisonId"]
    assert first["layer1Facts"] == second["layer1Facts"]


def test_compare_payload_stays_bounded() -> None:
    left = _version(
        "ver_left0001",
        tasks=[f"任务{index:03d}" for index in range(1, 41)],
        total_cost=5_000_000,
    )
    right = _version(
        "ver_right001",
        tasks=[f"任务{index:03d}" for index in range(30, 70)],
        total_cost=4_000_000,
    )

    result = compare_versions(left, right, left_number=1, right_number=2)

    assert result["payloadBytes"] < 16_000
    assert len(result["layer2Drivers"]["taskSet"]["onlyLeft"]) == 12
    assert result["layer2Drivers"]["taskSet"]["onlyLeftCount"] == 29


def test_compare_summary_is_usable_without_an_llm() -> None:
    left = _version("ver_left0001", tasks=["任务001", "任务002"], total_cost=5_000_000)
    right = _version("ver_right001", tasks=["任务001"], total_cost=4_000_000)

    payload = compare_versions(left, right, left_number=1, right_number=2)
    summary = render_compare_summary(payload)

    assert "方案01" in summary and "方案02" in summary
    assert "总成本" in summary
    assert "不能直接宣称节省" in summary
