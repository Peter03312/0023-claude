# 凸版号码机走票核对（Vue 3 + FastAPI）

票据印刷机长试走凸版号码机时，废张若被误算成一次有效压印，后续整批号码都会提前，
而肉眼只看首尾样张很难发现偏移起点。本应用让机长按纸序录入**计划**与**逐张实测**，
后端独立生成期望轨迹并逐张核对，直接标出**首个错号发生在哪张**，以及**废张之后是否
仍按原号码续印**。

## 走票规则

- 号码始终补足六位（`000000`~`999999`）。
- **第一个非废张使用起号**。
- 同号压满「每号有效印次」（1~3 次）后，**下一张非废张**才按方向变化一号
  （`asc` 递增 / `desc` 递减）。
- **计划废张**期望值固定为 `SPOIL`：既不改变当前号码，也不消耗有效印次。
  即使废张紧挨着换号点，换号也要推迟到下一张非废张开印时发生。
- 若完整计划会**越过 `000000` 或 `999999`**，整单拒绝，**不返回任何部分轨迹**。
- 实测行数必须等于计划张数；计划废张行写号码、或普通行写 `SPOIL`，均判不匹配。

### 示例

起号 `000001`、递增、每号 2 次、6 张纸、第 4 张为计划废张：

| 纸序 | 类型 | 当前号码 | 印次进度 | 期望 |
| ---: | --- | --- | --- | --- |
| 1 | 有效 | 000001 | 1/2 | 000001 |
| 2 | 有效 | 000001 | 2/2 | 000001 |
| 3 | 有效 | 000002 | 1/2 | 000002 |
| 4 | 废张 | 000002 | — | SPOIL |
| 5 | 有效 | 000002 | 2/2 | 000002 |
| 6 | 有效 | 000003 | 1/2 | 000003 |

## 目录结构

```
.
├── backend/            FastAPI 服务
│   ├── app/
│   │   ├── trajectory.py      # 核心轨迹逻辑（与测试同目录）
│   │   ├── test_trajectory.py # 核心逻辑单元测试
│   │   ├── test_api.py        # HTTP 层联调测试
│   │   └── main.py            # /api/generate、/api/verify
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/           Vue 3 + Vite
│   ├── src/App.vue
│   ├── Dockerfile      # 多阶段构建，nginx 托管并反代 /api
│   └── nginx.conf
├── verify/             一次性验收服务（仅用 Python 标准库）
│   ├── acceptance.py
│   └── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 用 Docker Compose 运行

```bash
# 构建并启动前后端（前端默认宿主端口 8080）
docker compose up --build frontend backend

# 另开终端运行一次性验收（对在跑的服务做真实 HTTP 联调，跑完即退出）
docker compose up --build verify
```

覆盖前端宿主端口：

```bash
WEB_PORT=9090 docker compose up --build
# 或
cp .env.example .env   # 在 .env 中修改 WEB_PORT
```

打开 `http://localhost:${WEB_PORT:-8080}`：

1. 录入六位起号、方向、每号有效印次（1~3）、计划纸张数（1~200）、
   严格升序且不重复的计划废张序号；
2. 在逐张实测区录入每张的六位号码或 `SPOIL`（计划废张行会自动预填 `SPOIL`，可改）；
3. 提交后按纸序展示：纸序、类型、当前号码、有效印次进度、期望、实测、判定原因；
   横幅直接给出首个错号纸序与全部差异纸序，差异行红色标出。

## API

### `POST /api/verify` 生成轨迹并核对

请求：

```json
{
  "start": "000001",
  "direction": "asc",
  "copies": 2,
  "sheet_count": 6,
  "spoil_sheets": [4],
  "actuals": ["000001", "000001", "000002", "SPOIL", "000002", "000003"]
}
```

成功返回 `all_match`、`mismatch_sheets`、`first_mismatch_sheet` 以及逐张 `rows`。
任何参数/越界/实测错误均返回 `400`：

```json
{ "error": "完整计划将递增越过 999999（末号约为 1000000），整单拒绝。", "field": "plan" }
```

`field` 精确定位到 `start` / `direction` / `copies` / `sheet_count` /
`spoil_sheets` / `actuals`，前端据此在对应表单位置显示错误。

### `POST /api/generate`

只生成期望轨迹（不含实测核对），字段同上但无需 `actuals`。

### `GET /api/health`

返回 `{"status":"ok"}`。

## 本地开发（不用 Docker）

```bash
# 后端
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（vite 已把 /api 代理到 localhost:8000）
cd frontend
npm install
npm run dev
```

### 测试

核心代码旁提供 pytest 测试（40+ 用例，覆盖换号时机、废张不消耗印次、
边界 000000/999999、越界整单拒绝、实测长度/类型错误等）：

```bash
cd backend
pip install -r requirements.txt
python -m pytest -q
```

`verify/acceptance.py` 是 compose 中 `verify` 服务运行的一次性验收脚本。
它用**独立实现的轨迹算法**通过真实 HTTP 调用交叉核对前后端，而非比对固定响应；
本地也可对已启动的服务手动运行（脚本中的主机名为 compose 网络名，
脱离 compose 时改为 localhost 即可）。
