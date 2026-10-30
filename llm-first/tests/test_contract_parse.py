"""产物解析的边界：模型爱在 JSON 后面补一段「说明」，那时该怎么办。

这是实测踩出来的：67 条 core 里 20 多条首轮失败报 `Extra data`，
模型交回来的东西长这样——

    ```json
    {"route": "dsl", ...}
    ```

    说明：
    - 「钱」按默认口径落成现金支出……

JSON 本身完整、无歧义，后面那段是它自己的解释。原来的解析只剥两端的围栏，
于是整段扔给 json.loads 直接炸，接着烧掉一整轮修复（一次完整 prompt 的
模型调用）去换一份一模一样的内容。这类"格式洁癖"是纯成本，不换来任何正确性。

但放行必须窄：**第二份 JSON 不能当说明扔掉**。那是数据，丢掉会让后面的判据
在少了一半的产物上判"通过"——那比报错严重得多。
"""
from __future__ import annotations

import pytest

import contract


def test_plain_object_still_parses() -> None:
    assert contract.parse_object('{"route": "dsl"}') == {"route": "dsl"}


def test_fenced_object_parses() -> None:
    assert contract.parse_object('```json\n{"route": "dsl"}\n```') == {"route": "dsl"}


def test_trailing_prose_after_fence_is_tolerated() -> None:
    text = '```json\n{"route": "dsl"}\n```\n\n说明：\n- 现金支出不含发电损失。'
    assert contract.parse_object(text) == {"route": "dsl"}


def test_trailing_prose_without_fence_is_tolerated() -> None:
    text = '{"route": "dsl"}\n\n这里解释一下为什么这么落。'
    assert contract.parse_object(text) == {"route": "dsl"}


def test_second_object_is_not_treated_as_prose() -> None:
    """第二份 JSON 是数据，不是解释——必须报错，不能悄悄丢。"""
    text = '{"scenario": "A"}\n{"scenario": "B"}'
    with pytest.raises(ValueError):
        contract.parse_object(text)


def test_second_fenced_object_is_not_treated_as_prose() -> None:
    text = '```json\n{"scenario": "A"}\n```\n```json\n{"scenario": "B"}\n```'
    with pytest.raises(ValueError):
        contract.parse_object(text)


def test_second_array_is_not_treated_as_prose() -> None:
    text = '{"route": "dsl"}\n[{}, {}]'
    with pytest.raises(ValueError):
        contract.parse_object(text)


def test_pure_prose_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract.parse_object("这个需求我做不到，因为系统里没有对应的原语。")


def test_empty_text_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract.parse_object("")


def test_top_level_array_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract.parse_object("[1, 2, 3]")


def test_broken_json_is_rejected() -> None:
    with pytest.raises(ValueError):
        contract.parse_object('{"route": "dsl",,}')
