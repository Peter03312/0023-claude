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

    print("[4] 核对区间：跨废张与换号点，号码承接且差异用绝对纸序")
    # 计划完整轨迹：1:000001(1/2) 2:000001(2/2) 3:000002(1/2) 4:SPOIL 5:000002(2/2) 6:000003(1/2)
    range_plan = {"start": "000001", "direction": "asc", "copies": 2,
                  "sheet_count": 6, "spoil_sheets": [4]}
    full = independent_trajectory(1, "asc", 2, 6, [4])

    # 区间 3~5：从换号点开始、跨废张，首行期望与印次进度承接前序纸张
    status, body = request("POST", f"{BACKEND}/api/verify", {
        **range_plan, "check_from": 3, "check_to": 5,
        "actuals": ["000002", "SPOIL", "000002"]})
    want = [(r["sheet_no"], r["expected"]) for r in full[2:5]]
    got = [(r["sheet_no"], r["expected"]) for r in body["rows"]]
    check("区间 3~5 期望轨迹与独立推算切片一致且核对通过",
          status == 200 and body["all_match"] is True and got == want
          and [r["impression"] for r in body["rows"]] == ["1/2", "—", "2/2"],
          json.dumps(body, ensure_ascii=False))
    check("区间回显为 3~5",
          body["check_from"] == 3 and body["check_to"] == 5)

    # 区间 4~6：从废张开始，换号仍推迟到第 6 张，而不是从区间起点重新起算
    status, body = request("POST", f"{BACKEND}/api/verify", {
        **range_plan, "check_from": 4, "check_to": 6,
        "actuals": ["SPOIL", "000002", "000003"]})
    check("区间从废张开始时首行期望 SPOIL 且后续按原号码续印",
          status == 200 and body["all_match"] is True
          and [r["expected"] for r in body["rows"]] == ["SPOIL", "000002", "000003"]
          and body["rows"][2]["impression"] == "1/2")

    # 区间 2~3：从同号中段开始，印次进度承接（第 2 张为 2/2）
    status, body = request("POST", f"{BACKEND}/api/verify", {
        **range_plan, "check_from": 2, "check_to": 3,
        "actuals": ["000001", "000002"]})
    check("区间从同号中段开始时印次进度承接",
          status == 200 and body["all_match"] is True
          and body["rows"][0]["impression"] == "2/2")

    # 区间 3~5 内第 5 张出错：差异必须报计划中的绝对纸序 5
    status, body = request("POST", f"{BACKEND}/api/verify", {
        **range_plan, "check_from": 3, "check_to": 5,
        "actuals": ["000002", "SPOIL", "000009"]})
    check("区间内差异使用计划中的绝对纸序",
          status == 200 and body["first_mismatch_sheet"] == 5
          and body["mismatch_sheets"] == [5])

    print("[5] 非法区间整单拒绝，不产生可误认的局部结果")
    for name, extra in (
        ("起始纸序超出计划", {"check_from": 0, "check_to": 3,
                            "actuals": ["000001", "000001", "000002"]}),
        ("结束纸序超出计划", {"check_from": 2, "check_to": 7,
                            "actuals": ["000001"] * 6}),
        ("起止前后倒置", {"check_from": 5, "check_to": 3,
                        "actuals": ["000002", "SPOIL", "000002"]}),
        ("实测数量不等于区间长度", {"check_from": 3, "check_to": 5,
                                "actuals": ["000002", "SPOIL"]}),
    ):
        status, body = request("POST", f"{BACKEND}/api/verify",
                               {**range_plan, **extra})
        check(f"{name}：400 且无 rows",
              status == 400 and "rows" not in body and "error" in body,
              json.dumps(body, ensure_ascii=False))

    status, body = request("POST", f"{BACKEND}/api/verify", {
        **range_plan, "check_from": 0, "check_to": 3,
        "actuals": ["000001", "000001", "000002"]})
    check("起始纸序越界时 field 定位到 check_from",
          status == 400 and body["field"] == "check_from")
    status, body = request("POST", f"{BACKEND}/api/verify", {
        **range_plan, "check_from": 3, "check_to": 5,
        "actuals": ["000002", "SPOIL"]})
    check("实测数量不符时 field 定位到 actuals",
          status == 400 and body["field"] == "actuals")

    print("[6] 递减 + 废张后续印原号")
    status, body = request("POST", f"{BACKEND}/api/verify", {
        "start": "000010", "direction": "desc", "copies": 2,
        "sheet_count": 4, "spoil_sheets": [3],
        "actuals": ["000010", "000010", "SPOIL", "000009"]})
    check("废张后按原号码续印且核对通过",
          status == 200 and body["all_match"] is True)

    print("[7] 废张行写号码 / 普通行写 SPOIL 均判不匹配")
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

    print("[8] 越过 999999 / 000000 整单拒绝且无部分轨迹")
    status, body = request("POST", f"{BACKEND}/api/generate", {
        "start": "999999", "direction": "asc", "copies": 1,
        "sheet_count": 2, "spoil_sheets": []})
    check("递增越界返回 400 且不含 rows",
          status == 400 and "rows" not in body and body["field"] == "plan")

    status, body = request("POST", f"{BACKEND}/api/generate", {
        "start": "000000", "direction": "desc", "copies": 1,
        "sheet_count": 2, "spoil_sheets": []})
    check("递减越界返回 400", status == 400 and "rows" not in body)

    print("[9] 错误反馈：实测行数、废张序、非法值")
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

    print("[10] 恰好落在边界允许")
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
