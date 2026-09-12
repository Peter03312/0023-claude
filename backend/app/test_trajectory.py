"""核心轨迹逻辑测试（与 trajectory.py 同目录）。"""
from __future__ import annotations

import pytest

from .trajectory import (
    PlanError,
    build_trajectory,
    normalize_plan,
    verify_plan,
)


def make(start="000001", direction="asc", copies=1, sheet_count=1, spoil_sheets=()):
    return normalize_plan(start, direction, copies, sheet_count, list(spoil_sheets))


def row_numbers(rows):
    return [(r.sheet_no, r.number, r.expected, r.impression) for r in rows]


# ---------- 轨迹生成 ----------

def test_first_normal_sheet_uses_start():
    plan = make(start="000100", sheet_count=1)
    rows = build_trajectory(plan)
    assert rows[0].number == "000100"
    assert rows[0].expected == "000100"


def test_copies_2_advances_after_two_normal_impressions():
    # 第 4 张是废张：不换号、不消耗印次
    plan = make(start="000001", copies=2, sheet_count=6, spoil_sheets=[4])
    rows = build_trajectory(plan)
    assert row_numbers(rows) == [
        (1, "000001", "000001", "1/2"),
        (2, "000001", "000001", "2/2"),
        (3, "000002", "000002", "1/2"),
        (4, "000002", "SPOIL", "—"),
        (5, "000002", "000002", "2/2"),
        (6, "000003", "000003", "1/2"),
    ]


def test_copies_3_with_spoil_keeps_number_and_progress():
    plan = make(start="000005", copies=3, sheet_count=5, spoil_sheets=[1, 3])
    rows = build_trajectory(plan)
    assert [(r.sheet_no, r.number, r.impression, r.expected) for r in rows] == [
        (1, "000005", "—", "SPOIL"),
        (2, "000005", "1/3", "000005"),
        (3, "000005", "—", "SPOIL"),
        (4, "000005", "2/3", "000005"),
        (5, "000005", "3/3", "000005"),
    ]


def test_descending_direction():
    plan = make(start="000010", direction="desc", copies=2, sheet_count=4)
    rows = build_trajectory(plan)
    assert [r.expected for r in rows] == ["000010", "000010", "000009", "000009"]


def test_leading_zeros_padded_to_six_digits():
    plan = make(start="000000", copies=1, sheet_count=3)
    rows = build_trajectory(plan)
    assert [r.number for r in rows] == ["000000", "000001", "000002"]


def test_start_at_999999_copies1_two_sheets_overflows():
    plan = make(start="999999", copies=1, sheet_count=2)
    with pytest.raises(PlanError, match="999999"):
        build_trajectory(plan)


def test_desc_crossing_zero_rejected_entirely():
    plan = make(start="000000", direction="desc", copies=1, sheet_count=1)
    # 单张恰好 000000 允许
    assert build_trajectory(plan)[0].number == "000000"

    plan2 = make(start="000000", direction="desc", copies=2, sheet_count=3)
    # 需要号码 000000、-1 → 越过 000000
    with pytest.raises(PlanError, match="000000"):
        build_trajectory(plan2)


def test_landing_exactly_on_boundary_is_allowed():
    plan = make(start="999998", copies=1, sheet_count=2)
    rows = build_trajectory(plan)
    assert [r.expected for r in rows] == ["999998", "999999"]

    plan2 = make(start="000001", direction="desc", copies=1, sheet_count=2)
    rows2 = build_trajectory(plan2)
    assert [r.expected for r in rows2] == ["000001", "000000"]


def test_partial_last_number_reserved_when_copies_exceed_remainder():
    # 4 个非废张、copies=3：需要号码 n、n+1，即使末号只印 1 次
    plan = make(start="999999", copies=3, sheet_count=4)
    with pytest.raises(PlanError):
        build_trajectory(plan)


def test_spoil_sheets_do_not_conserve_offset_at_end():
    # 5 张中 2 张废张，copies=3：3 个非废张同号，不换号
    plan = make(start="000007", copies=3, sheet_count=5, spoil_sheets=[2, 5])
    rows = build_trajectory(plan)
    assert {r.number for r in rows} == {"000007"}


# ---------- 参数校验 ----------

@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": "12345"},          # 不足六位
        {"start": "00000a"},
        {"start": 100001},           # 必须是字符串
        {"direction": "up"},
        {"copies": 0},
        {"copies": 4},
        {"copies": "1"},
        {"sheet_count": 0},
        {"sheet_count": 201},
        {"spoil_sheets": [4]},       # 超出 sheet_count=3
        {"spoil_sheets": [1, 1]},    # 重复
        {"spoil_sheets": [2, 1]},    # 非升序
        {"spoil_sheets": "1,2"},
    ],
)
def test_invalid_params_rejected(kwargs):
    defaults = dict(
        start="000001",
        direction="asc",
        copies=1,
        sheet_count=3,
        spoil_sheets=[],
    )
    defaults.update(kwargs)
    with pytest.raises(PlanError):
        normalize_plan(**defaults)


def test_bool_is_not_an_integer():
    with pytest.raises(PlanError):
        normalize_plan("000001", "asc", True, 3, [])
    with pytest.raises(PlanError):
        normalize_plan("000001", "asc", 1, 3, [True])


def test_all_spoils_rejected():
    with pytest.raises(PlanError, match="废张"):
        make(sheet_count=2, spoil_sheets=[1, 2])


def test_error_carries_field_name():
    try:
        normalize_plan("000001", "asc", 9, 3, [])
    except PlanError as exc:
        assert exc.field == "copies"
    else:
        raise AssertionError("应当抛错")


# ---------- 实测核对 ----------

def test_verify_all_match():
    result = verify_plan(
        "000001", "asc", 2, 5, [3],
        ["000001", "000001", "SPOIL", "000002", "000002"],
    )
    assert result.all_match is True
    assert result.mismatch_sheets == ()
    assert all(r.match for r in result.rows)


def test_verify_detects_first_mismatch_and_continues_numbering():
    # 第 2 张被误当有效压印后整批提前：机长应看到首个错号位置
    result = verify_plan(
        "000001", "asc", 1, 4, [],
        ["000001", "000003", "000003", "000004"],  # 第 2 张起整体提前一号
    )
    assert result.all_match is False
    assert result.mismatch_sheets[0] == 2
    assert result.rows[1].mismatch_reason and "000002" in result.rows[1].mismatch_reason


def test_verify_number_on_planned_spoil_is_mismatch():
    result = verify_plan(
        "000001", "asc", 2, 3, [2],
        ["000001", "000001", "000001"],  # 废张行写了号码
    )
    assert result.rows[1].match is False
    assert "废张" in result.rows[1].mismatch_reason
    # 废张不换号：第 3 张仍是 000001 的第 2/2 印次
    assert result.rows[2].number == "000001"
    assert result.rows[2].impression == "2/2"
    assert result.rows[2].match is True


def test_verify_spoil_on_normal_row_is_mismatch():
    result = verify_plan(
        "000001", "asc", 1, 2, [], ["000001", "SPOIL"],
    )
    assert result.rows[1].match is False
    assert "SPOIL" in result.rows[1].mismatch_reason


def test_verify_actuals_wrong_length_rejected():
    with pytest.raises(PlanError, match="实测行数"):
        verify_plan("000001", "asc", 1, 3, [], ["000001", "000002"])


def test_verify_illegal_actual_value_rejected():
    with pytest.raises(PlanError):
        verify_plan("000001", "asc", 1, 2, [], ["000001", "12"])
    with pytest.raises(PlanError):
        verify_plan("000001", "asc", 1, 2, [], ["000001", 100002])


def test_verify_lowercase_spoil_accepted():
    result = verify_plan(
        "000001", "asc", 1, 2, [2], ["000001", "spoil"],
    )
    assert result.all_match is True


def test_overflow_rejected_without_partial_trajectory():
    # 越界时整单拒绝，即便实测本身格式合法
    with pytest.raises(PlanError):
        verify_plan("999999", "asc", 1, 2, [], ["999999", "000000"])
