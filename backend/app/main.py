"""FastAPI 入口：号码机走票轨迹生成与实测核对。"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .trajectory import PlanError, build_trajectory, normalize_plan, verify_plan

app = FastAPI(title="号码机走票核对 API", version="1.0.0")

_PLAN_FIELDS = ("start", "direction", "copies", "sheet_count", "spoil_sheets")


@app.exception_handler(PlanError)
async def plan_error_handler(_request: Request, exc: PlanError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": exc.message, "field": exc.field},
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _extract_plan_fields(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlanError("请求体必须是 JSON 对象。", "plan")
    missing = [name for name in _PLAN_FIELDS if name not in payload]
    if missing:
        raise PlanError(f"缺少必填字段：{', '.join(missing)}。", missing[0])
    return {name: payload[name] for name in _PLAN_FIELDS}


def _expected_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "sheet_no": row.sheet_no,
        "is_spoil": row.is_spoil,
        "number": row.number,
        "expected": row.expected,
        "impression": row.impression,
    }


def _actual_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "sheet_no": row.sheet_no,
        "is_spoil": row.is_spoil,
        "number": row.number,
        "expected": row.expected,
        "actual": row.actual,
        "impression": row.impression,
        "match": row.match,
        "mismatch_reason": row.mismatch_reason,
    }


@app.post("/api/generate")
async def generate(request: Request) -> dict[str, Any]:
    """仅生成期望轨迹（不核对实测）。"""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "请求体不是合法的 JSON。", "field": "plan"},
        )

    plan = normalize_plan(**_extract_plan_fields(payload))
    rows = build_trajectory(plan)
    return {
        "plan": {
            "start": f"{plan.start:06d}",
            "direction": plan.direction,
            "copies": plan.copies,
            "sheet_count": plan.sheet_count,
            "spoil_sheets": list(plan.spoil_sheets),
        },
        "rows": [_expected_row_to_dict(row) for row in rows],
    }


@app.post("/api/verify")
async def verify(request: Request) -> dict[str, Any]:
    """生成轨迹并逐张核对实测，返回全部差异（按纸序）。"""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "请求体不是合法的 JSON。", "field": "plan"},
        )

    if not isinstance(payload, dict) or "actuals" not in payload:
        return JSONResponse(
            status_code=400,
            content={"error": "缺少必填字段：actuals。", "field": "actuals"},
        )

    fields = _extract_plan_fields(payload)
    result = verify_plan(
        actuals=payload["actuals"],
        check_from=payload.get("check_from"),
        check_to=payload.get("check_to"),
        **fields,
    )

    return {
        "plan": {
            "start": f"{result.plan.start:06d}",
            "direction": result.plan.direction,
            "copies": result.plan.copies,
            "sheet_count": result.plan.sheet_count,
            "spoil_sheets": list(result.plan.spoil_sheets),
        },
        "check_from": result.check_from,
        "check_to": result.check_to,
        "all_match": result.all_match,
        "mismatch_sheets": list(result.mismatch_sheets),
        "first_mismatch_sheet": (
            result.mismatch_sheets[0] if result.mismatch_sheets else None
        ),
        "rows": [_actual_row_to_dict(row) for row in result.rows],
    }
