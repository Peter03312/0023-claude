#!/usr/bin/env python3
"""一次性验收脚本：对已运行的 frontend / backend 做真实 HTTP 联调验收。

不做固定响应比对，而是用本脚本内置的独立轨迹算法动态推算期望值，
通过 docker compose 中的 verify 服务运行一次：全部通过退出码 0，否则 1。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

FRONTEND = os.environ.get("FRONTEND_URL", "http://frontend:80")
BACKEND = os.environ.get("BACKEND_URL", "http://backend:8000")

failures: list[str] = []
checks = 0


def independent_trajectory(start: int, direction: str, copies: int,
                           sheet_count: int, spoil_sheets: list[int]):
    """验收端独立实现的期望轨迹，避免与被测代码共用逻辑。"""
    step = 1 if direction == "asc" else -1
    spoil = set(spoil_sheets)
    rows = []
    current, made = start, 0
    for sheet_no in range(1, sheet_count + 1):
        if sheet_no in spoil:
            rows.append({"sheet_no": sheet_no, "is_spoil": True,
                         "number": current, "expected": "SPOIL"})
            continue
        if made > 0 and made % copies == 0:
            current += step
        rows.append({"sheet_no": sheet_no, "is_spoil": False,
                     "number": current, "expected": f"{current:06d}"})
        made += 1
    return rows


def request(method: str, url: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def check(name: str, condition: bool, detail: str = ""):
    global checks
    checks += 1
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def wait_for_services():
    for name, url in (("backend", f"{BACKEND}/api/health"),
                      ("frontend", f"{FRONTEND}/")):
        for attempt in range(30):
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(2)
        else:
            print(f"FATAL: {name} 在 60 秒内未就绪：{url}")
            sys.exit(1)


def main():
    print("等待 frontend / backend 就绪…")
    wait_for_services()

    print("[1] 健康检查与前端页面")
    status, body = request("GET", f"{BACKEND}/api/health")
    check("GET /api/health 返回 200/ok", status == 200 and body == {"status": "ok"})

    with urllib.request.urlopen(f"{FRONTEND}/", timeout=5) as resp:
        html = resp.read().decode()
    check("前端首页返回且挂载 Vue 入口",
          resp.status == 200 and 'id="app"' in html
          and ('"/assets/' in html or "main.js" in html))

    print("[2] 含废张、copies=2 的完整轨迹（独立算法交叉核对）")
    plan = {"start": "000001", "direction": "asc", "copies": 2,
            "sheet_count": 6, "spoil_sheets": [4]}
    status, body = request("POST", f"{BACKEND}/api/generate", plan)
    expected = independent_trajectory(1, "asc", 2, 6, [4])
    got = [(r["sheet_no"], int(r["number"]) if not r["is_spoil"] else None,
            r["expected"], r["is_spoil"]) for r in body["rows"]]
    want = [(r["sheet_no"], None if r["is_spoil"] else r["number"],
             r["expected"], r["is_spoil"]) for r in expected]
    check("/api/generate 轨迹与独立推算一致", status == 200 and got == want,
          json.dumps(body, ensure_ascii=False))
    check("废张印次进度显示为 —", body["rows"][3]["impression"] == "—")

    print("[3] 实测核对：首个错号定位")
    # 第 2 张被多压一次号：期望 000002 却实测 000003（第 3、4 张碰巧与期望同号）
    actuals = ["000001", "000003", "000003", "000004"]
    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000001", "direction": "asc", "copies": 1,
        "sheet_count": 4, "spoil_sheets": [], "actuals": actuals})
    check("首个不匹配定位在第 2 张",
          status == 200 and body["first_mismatch_sheet"] == 2
          and body["mismatch_sheets"] == [2])
    check("差异行包含明确原因",
          "000002" in body["rows"][1]["mismatch_reason"])

    # 整体提前一号：第 2 张起全部偏移
    actuals_shift = ["000001", "000003", "000004", "000005"]
    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000001", "direction": "asc", "copies": 1,
        "sheet_count": 4, "spoil_sheets": [], "actuals": actuals_shift})
    check("整批提前时差异按纸序全部标出",
          status == 200 and body["mismatch_sheets"] == [2, 3, 4])

    print("[4] 递减 + 废张后续印原号")
    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000010", "direction": "desc", "copies": 2,
        "sheet_count": 4, "spoil_sheets": [3],
        "actuals": ["000010", "000010", "SPOIL", "000009"]})
    check("废张后按原号码续印且核对通过",
          status == 200 and body["all_match"] is True)

    print("[5] 废张行写号码 / 普通行写 SPOIL 均判不匹配")
    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000001", "direction": "asc", "copies": 2,
        "sheet_count": 3, "spoil_sheets": [2],
        "actuals": ["000001", "000001", "000001"]})
    check("计划废张行写号码判不匹配，但后续仍按原号续印",
          status == 200 and body["rows"][1]["match"] is False
          and body["rows"][2]["match"] is True
          and body["rows"][2]["impression"] == "2/2")

    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000001", "direction": "asc", "copies": 1,
        "sheet_count": 2, "spoil_sheets": [],
        "actuals": ["000001", "SPOIL"]})
    check("普通行写 SPOIL 判不匹配",
          status == 200 and body["rows"][1]["match"] is False)

    print("[6] 越过 999999 / 000000 整单拒绝且无部分轨迹")
    status, body = request("POST", f"{BACKEND}/api/generate", {
        "start": "999999", "direction": "asc", "copies": 1,
        "sheet_count": 2, "spoil_sheets": []})
    check("递增越界返回 400 且不含 rows",
          status == 400 and "rows" not in body and body["field"] == "plan")

    status, body = request("POST", f"{BACKEND}/api/generate", {
        "start": "000000", "direction": "desc", "copies": 1,
        "sheet_count": 2, "spoil_sheets": []})
    check("递减越界返回 400", status == 400 and "rows" not in body)

    print("[7] 错误反馈：实测行数、废张序、非法值")
    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000001", "direction": "asc", "copies": 1,
        "sheet_count": 3, "spoil_sheets": [], "actuals": ["000001"]})
    check("实测行数不等于计划张数被拒",
          status == 400 and body["field"] == "actuals" and "实测行数" in body["error"])

    status, body = request("POST", f"{BACKEND}/api/generate", {
        "start": "000001", "direction": "asc", "copies": 1,
        "sheet_count": 5, "spoil_sheets": [4, 2]})
    check("废张序号非升序被拒并带字段名",
          status == 400 and body["field"] == "spoil_sheets")

    status, body = request("POST", f"{BACKEND}/api/generate", {
        "start": "000001", "direction": "asc", "copies": 1,
        "sheet_count": 3, "spoil_sheets": [2, 2]})
    check("废张序号重复被拒", status == 400)

    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000001", "direction": "asc", "copies": 1,
        "sheet_count": 2, "spoil_sheets": [],
        "actuals": ["1", "000002"]})
    check("非法实测值被拒", status == 400 and body["field"] == "actuals")

    print("[8] 恰好落在边界允许")
    status, body = request("POST", f"{BACKEND}/api/generate", {
        "start": "999998", "direction": "asc", "copies": 1,
        "sheet_count": 2, "spoil_sheets": []})
    check("末号恰好 999999 允许",
          status == 200 and [r["expected"] for r in body["rows"]] == ["999998", "999999"])

    print(f"\n共 {checks} 项验收，失败 {len(failures)} 项。")
    if failures:
        print("未通过：" + ", ".join(failures))
        sys.exit(1)
    print("全部验收通过 ✅")


if __name__ == "__main__":
    main()
