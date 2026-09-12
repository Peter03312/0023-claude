"""/api/generate 与 /api/verify 的 HTTP 层测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from .main import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_generate_endpoint_full_flow():
    resp = client.post(
        "/api/generate",
        json={
            "start": "000100",
            "direction": "asc",
            "copies": 2,
            "sheet_count": 4,
            "spoil_sheets": [2],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["expected"] for r in body["rows"]] == [
        "000100",
        "SPOIL",
        "000100",
        "000101",
    ]
    assert body["rows"][1]["impression"] == "—"


def test_verify_endpoint_reports_mismatch_sheets():
    resp = client.post(
        "/api/verify",
        json={
            "start": "000001",
            "direction": "asc",
            "copies": 1,
            "sheet_count": 3,
            "spoil_sheets": [],
            "actuals": ["000001", "000009", "000003"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_match"] is False
    assert body["mismatch_sheets"] == [2]
    assert body["first_mismatch_sheet"] == 2


def test_verify_desc_with_spoil_resumes_same_number():
    resp = client.post(
        "/api/verify",
        json={
            "start": "000010",
            "direction": "desc",
            "copies": 2,
            "sheet_count": 4,
            "spoil_sheets": [3],
            "actuals": ["000010", "000010", "SPOIL", "000009"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_match"] is True


def test_overflow_returns_400_without_rows():
    resp = client.post(
        "/api/generate",
        json={
            "start": "999999",
            "direction": "asc",
            "copies": 1,
            "sheet_count": 2,
            "spoil_sheets": [],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body and "field" in body
    assert "rows" not in body


def test_field_specific_errors():
    resp = client.post(
        "/api/verify",
        json={
            "start": "12",
            "direction": "asc",
            "copies": 1,
            "sheet_count": 2,
            "spoil_sheets": [],
            "actuals": ["000001", "000002"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["field"] == "start"


def test_actuals_length_error():
    resp = client.post(
        "/api/verify",
        json={
            "start": "000001",
            "direction": "asc",
            "copies": 1,
            "sheet_count": 3,
            "spoil_sheets": [],
            "actuals": ["000001"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["field"] == "actuals"


def test_missing_field_error():
    resp = client.post(
        "/api/generate",
        json={"start": "000001", "direction": "asc", "copies": 1, "sheet_count": 1},
    )
    assert resp.status_code == 400
    assert resp.json()["field"] == "spoil_sheets"


def test_malformed_json_body():
    resp = client.post(
        "/api/generate",
        content="{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["field"] == "plan"


# ---------- 核对区间（check_from / check_to） ----------

RANGE_PLAN = {
    "start": "000001",
    "direction": "asc",
    "copies": 2,
    "sheet_count": 6,
    "spoil_sheets": [4],
}
# 完整轨迹：1:000001(1/2) 2:000001(2/2) 3:000002(1/2) 4:SPOIL 5:000002(2/2) 6:000003(1/2)


def test_verify_without_range_keeps_legacy_behavior():
    resp = client.post(
        "/api/verify",
        json={
            **RANGE_PLAN,
            "actuals": ["000001", "000001", "000002", "SPOIL", "000002", "000003"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_match"] is True
    assert body["check_from"] == 1 and body["check_to"] == 6
    assert [r["sheet_no"] for r in body["rows"]] == [1, 2, 3, 4, 5, 6]


def test_verify_range_across_spoil_and_number_change():
    resp = client.post(
        "/api/verify",
        json={
            **RANGE_PLAN,
            "check_from": 3,
            "check_to": 5,
            "actuals": ["000002", "SPOIL", "000002"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_match"] is True
    assert (body["check_from"], body["check_to"]) == (3, 5)
    assert [
        (r["sheet_no"], r["expected"], r["impression"]) for r in body["rows"]
    ] == [(3, "000002", "1/2"), (4, "SPOIL", "—"), (5, "000002", "2/2")]


def test_verify_range_from_spoil_start_continues_trajectory():
    resp = client.post(
        "/api/verify",
        json={
            **RANGE_PLAN,
            "check_from": 4,
            "check_to": 6,
            "actuals": ["SPOIL", "000002", "000003"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_match"] is True
    assert [r["expected"] for r in body["rows"]] == ["SPOIL", "000002", "000003"]


def test_verify_range_mismatch_reports_absolute_sheet_no():
    resp = client.post(
        "/api/verify",
        json={
            **RANGE_PLAN,
            "check_from": 3,
            "check_to": 5,
            "actuals": ["000002", "SPOIL", "000009"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["first_mismatch_sheet"] == 5
    assert body["mismatch_sheets"] == [5]


def test_verify_range_out_of_plan_rejected_without_rows():
    resp = client.post(
        "/api/verify",
        json={**RANGE_PLAN, "check_from": 0, "check_to": 3, "actuals": ["000001"]},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["field"] == "check_from" and "rows" not in body

    resp = client.post(
        "/api/verify",
        json={**RANGE_PLAN, "check_from": 2, "check_to": 7, "actuals": ["000001"]},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["field"] == "check_to" and "rows" not in body


def test_verify_inverted_range_rejected_without_rows():
    resp = client.post(
        "/api/verify",
        json={
            **RANGE_PLAN,
            "check_from": 5,
            "check_to": 3,
            "actuals": ["000002", "SPOIL", "000002"],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["field"] == "check_from" and "rows" not in body


def test_verify_range_actuals_count_mismatch_rejected():
    resp = client.post(
        "/api/verify",
        json={
            **RANGE_PLAN,
            "check_from": 3,
            "check_to": 5,
            "actuals": ["000002", "SPOIL"],  # 区间长度 3，实测只有 2 条
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["field"] == "actuals" and "rows" not in body
    assert "核对区间长度 3" in body["error"]
