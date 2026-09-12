"""号码机走票轨迹的核心领域逻辑。

业务规则
--------
* 号码始终为补足六位的 000000~999999。
* 第一个非废张使用起号；同号压印 ``copies`` 次后，下一张非废张才按
  方向（asc 递增 / desc 递减）变化一号。
* 计划废张（SPOIL）期望值固定为 SPOIL，既不改变当前号码，也不消耗有效印次。
* 若完整计划会越过 000000 或 999999，整单拒绝，不返回任何部分轨迹。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["asc", "desc"]

NUMBER_RE = re.compile(r"^\d{6}$")
MIN_NUMBER = 0
MAX_NUMBER = 999_999
MAX_SHEETS = 200


class PlanError(ValueError):
    """计划参数非法或越界。

    ``field`` 为出错字段名（plan 级错误用 ``"plan"``），供前端精确定位。
    """

    def __init__(self, message: str, field: str = "plan"):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True)
class Plan:
    start: int
    direction: Direction
    copies: int
    sheet_count: int
    spoil_sheets: tuple[int, ...]  # 1-based，严格升序且不重复

    @property
    def normal_count(self) -> int:
        return self.sheet_count - len(self.spoil_sheets)


@dataclass(frozen=True)
class ExpectedRow:
    sheet_no: int           # 纸序，1-based
    is_spoil: bool
    number: str             # 当前号码（废张为其前后续印的号码）
    expected: str           # 期望值：六位号码或 SPOIL
    impression: str         # 有效印次进度，如 "1/2"；废张为 "—"


@dataclass(frozen=True)
class ActualRow:
    sheet_no: int
    is_spoil: bool
    number: str
    expected: str
    actual: str
    impression: str
    match: bool
    mismatch_reason: str | None


@dataclass(frozen=True)
class VerifyResult:
    plan: Plan
    rows: tuple[ActualRow, ...]
    all_match: bool
    mismatch_sheets: tuple[int, ...]
    check_from: int             # 核对起始纸序（1-based，闭区间）
    check_to: int               # 核对结束纸序（1-based，闭区间）


def _as_int(value: Any, field: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanError(f"{label}必须是整数。", field)
    return value


def normalize_plan(
    start: Any,
    direction: Any,
    copies: Any,
    sheet_count: Any,
    spoil_sheets: Any,
) -> Plan:
    """校验并归一化请求参数。任何字段非法都抛出 :class:`PlanError`。"""
    if not isinstance(start, str) or not NUMBER_RE.match(start):
        raise PlanError("起号必须是恰好六位的数字（000000~999999）。", "start")

    if direction not in ("asc", "desc"):
        raise PlanError("方向只能是 asc（递增）或 desc（递减）。", "direction")

    copies = _as_int(copies, "copies", "每号有效印次")
    if not 1 <= copies <= 3:
        raise PlanError("每号有效印次必须在 1~3 之间。", "copies")

    sheet_count = _as_int(sheet_count, "sheet_count", "计划纸张数")
    if not 1 <= sheet_count <= MAX_SHEETS:
        raise PlanError("计划纸张数必须在 1~200 之间。", "sheet_count")

    if not isinstance(spoil_sheets, list) or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in spoil_sheets
    ):
        raise PlanError(
            "计划废张序号必须是整数数组。", "spoil_sheets"
        )

    normalized: list[int] = []
    for raw in spoil_sheets:
        if not 1 <= raw <= sheet_count:
            raise PlanError(
                f"废张序号 {raw} 超出纸序范围（1~{sheet_count}）。",
                "spoil_sheets",
            )
        normalized.append(raw)

    for prev, cur in zip(normalized, normalized[1:]):
        if cur == prev:
            raise PlanError(f"废张序号 {cur} 重复。", "spoil_sheets")
        if cur < prev:
            raise PlanError(
                f"废张序号必须严格升序：{cur} 出现在 {prev} 之前。",
                "spoil_sheets",
            )

    if sheet_count == len(normalized):
        raise PlanError(
            "全部纸张都是废张，没有任何有效压印，无法形成号码轨迹。",
            "spoil_sheets",
        )

    return Plan(
        start=int(start),
        direction=direction,  # type: ignore[arg-type]
        copies=copies,
        sheet_count=sheet_count,
        spoil_sheets=tuple(normalized),
    )


def _check_boundary(plan: Plan) -> None:
    """完整计划越过 000000/999999 时整单拒绝。"""
    # 需要的不同号码个数；copies=2、3 时末号可能印不满，但仍需为其预留号段
    distinct = (plan.normal_count + plan.copies - 1) // plan.copies
    if plan.direction == "asc":
        last_value = plan.start + distinct - 1
        if last_value > MAX_NUMBER:
            raise PlanError(
                f"完整计划将递增越过 999999（末号约为 {last_value:06d}），整单拒绝。",
                "plan",
            )
    else:
        last_value = plan.start - distinct + 1
        if last_value < MIN_NUMBER:
            raise PlanError(
                f"完整计划将递减越过 000000（末号约为 {last_value:06d}），整单拒绝。",
                "plan",
            )


def build_trajectory(plan: Plan) -> tuple[ExpectedRow, ...]:
    """生成期望轨迹；越界时抛出且不产生部分轨迹。"""
    _check_boundary(plan)

    step = 1 if plan.direction == "asc" else -1
    spoil_set = set(plan.spoil_sheets)
    rows: list[ExpectedRow] = []
    current = plan.start
    made = 0  # 已完成的非废张压印数

    for sheet_no in range(1, plan.sheet_count + 1):
        if sheet_no in spoil_set:
            # 废张：号码不动、有效印次不消耗（即使上一印次已凑满，也要等
            # 下一张“非废张”开印时才换号）
            rows.append(
                ExpectedRow(
                    sheet_no=sheet_no,
                    is_spoil=True,
                    number=f"{current:06d}",
                    expected="SPOIL",
                    impression="—",
                )
            )
            continue

        # 已凑满上一号的全部印次后，下一张非废张才按方向变化一号
        if made > 0 and made % plan.copies == 0:
            current += step

        impression_index = made % plan.copies + 1
        rows.append(
            ExpectedRow(
                sheet_no=sheet_no,
                is_spoil=False,
                number=f"{current:06d}",
                expected=f"{current:06d}",
                impression=f"{impression_index}/{plan.copies}",
            )
        )
        made += 1

    return tuple(rows)


def normalize_check_range(
    check_from: Any,
    check_to: Any,
    sheet_count: int,
) -> tuple[int, int]:
    """归一化核对区间（1-based 闭区间）。

    未传的一端取计划边界（起始默认 1、结束默认 sheet_count），
    因此未传区间的旧请求等价于覆盖完整计划。
    """
    lo = 1 if check_from is None else _as_int(check_from, "check_from", "核对起始纸序")
    hi = (
        sheet_count
        if check_to is None
        else _as_int(check_to, "check_to", "核对结束纸序")
    )
    if not 1 <= lo <= sheet_count:
        raise PlanError(
            f"核对起始纸序 {lo} 超出计划范围（1~{sheet_count}）。", "check_from"
        )
    if not 1 <= hi <= sheet_count:
        raise PlanError(
            f"核对结束纸序 {hi} 超出计划范围（1~{sheet_count}）。", "check_to"
        )
    if lo > hi:
        raise PlanError(
            f"核对起始纸序 {lo} 不能大于核对结束纸序 {hi}。", "check_from"
        )
    return lo, hi


def _normalize_actual(raw: Any) -> str:
    if not isinstance(raw, str):
        raise PlanError("每张实测值必须是字符串（六位号码或 SPOIL）。", "actuals")
    text = raw.strip().upper()
    if text != "SPOIL" and not NUMBER_RE.match(text):
        raise PlanError(
            f"实测值 {raw!r} 非法：只允许六位数字号码或 SPOIL。",
            "actuals",
        )
    return text


def verify_plan(
    start: Any,
    direction: Any,
    copies: Any,
    sheet_count: Any,
    spoil_sheets: Any,
    actuals: Any,
    check_from: Any = None,
    check_to: Any = None,
) -> VerifyResult:
    """生成轨迹并与逐张实测核对。任何错误都整单拒绝（不返回部分轨迹）。

    先按完整计划推进号码与有效印次，再截取 ``[check_from, check_to]``
    闭区间参与实测比对；因此区间从废张、同号中段或换号点开始时，
    首行期望与印次进度都承接前序纸张，而不是重新起算。
    未传区间时默认覆盖完整计划，行为与旧请求一致。
    """
    plan = normalize_plan(start, direction, copies, sheet_count, spoil_sheets)
    lo, hi = normalize_check_range(check_from, check_to, plan.sheet_count)

    if not isinstance(actuals, list):
        raise PlanError("实测记录必须是数组。", "actuals")
    expected_count = hi - lo + 1
    if len(actuals) != expected_count:
        if lo == 1 and hi == plan.sheet_count:
            raise PlanError(
                f"实测行数为 {len(actuals)}，必须等于计划张数 {plan.sheet_count}。",
                "actuals",
            )
        raise PlanError(
            f"实测行数为 {len(actuals)}，必须等于核对区间长度 "
            f"{expected_count}（第 {lo}~{hi} 张）。",
            "actuals",
        )

    normalized_actuals = [_normalize_actual(item) for item in actuals]

    # 参数与实测全部通过校验后才生成轨迹，确保越界时绝不返回部分轨迹
    expected_rows = build_trajectory(plan)[lo - 1 : hi]

    result_rows: list[ActualRow] = []
    mismatch_sheets: list[int] = []
    for row, actual in zip(expected_rows, normalized_actuals):
        reason: str | None = None
        if row.is_spoil and actual != "SPOIL":
            reason = "该张是计划废张，期望 SPOIL，却录入了号码。"
        elif not row.is_spoil and actual == "SPOIL":
            reason = "该张是有效压印，期望号码，却录入了 SPOIL。"
        elif not row.is_spoil and actual != row.expected:
            reason = f"号码不符：期望 {row.expected}，实测 {actual}。"

        if reason is not None:
            mismatch_sheets.append(row.sheet_no)
        result_rows.append(
            ActualRow(
                sheet_no=row.sheet_no,
                is_spoil=row.is_spoil,
                number=row.number,
                expected=row.expected,
                actual=actual,
                impression=row.impression,
                match=reason is None,
                mismatch_reason=reason,
            )
        )

    return VerifyResult(
        plan=plan,
        rows=tuple(result_rows),
        all_match=not mismatch_sheets,
        mismatch_sheets=tuple(mismatch_sheets),
        check_from=lo,
        check_to=hi,
    )
