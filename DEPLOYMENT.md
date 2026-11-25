# Deployment Information

## Public URL

https://glistening-creation-production.up.railway.app

Agent là API, không có trang chủ: `GET /` trả 404. Dùng các endpoint `/health`, `/ready`, `POST /ask` bên dưới.

## Platform

**Railway** — deploy thư mục `06-lab-complete/` bằng `railway up`. Railway đọc `06-lab-complete/railway.toml` (`builder = "DOCKERFILE"`, `healthcheckPath = "/health"`) và build bằng Dockerfile multi-stage của Part 6.

Project `glistening-creation`, environment `production`, gồm 2 service:

| Service | Vai trò |
|---|---|
| `glistening-creation` | Agent (FastAPI + uvicorn, image từ `06-lab-complete/Dockerfile`) |
| `Redis` | Redis của Railway (có volume), lưu state rate limit và cost guard |

Agent kết nối Redis qua mạng private của Railway (`redis.railway.internal:6379`).

## Test Commands

Đặt `BASE` là URL public, `AGENT_API_KEY` là key đã đặt trên Railway (không ghi key vào repo). Mỗi lệnh chỉ in mã HTTP.

```bash
BASE=https://glistening-creation-production.up.railway.app

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

### Kết quả đã chạy

| Lệnh | Mã HTTP | Body |
|---|---|---|
| `GET /health` | 200 | `{"status":"ok","environment":"production"}` |
| `GET /ready` | 200 | Redis kết nối được |
| `POST /ask` không có key | 401 | `{"detail":"Invalid or missing API key"}` |
| `POST /ask` có key | 200 | `{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05}}` |

## Environment Variables Set

Đặt trên Railway (tab **Variables** của service agent), không ghi trong code:

| Biến | Giá trị |
|---|---|
| `AGENT_API_KEY` | Chuỗi ngẫu nhiên, chỉ lưu trên Railway |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` — biến tham chiếu, Railway tự điền URL của service Redis |
| `ENVIRONMENT` | `production` |
| `PORT` | Railway tự cấp; app đọc `PORT` trong `CMD` của Dockerfile |

`RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`, `LOG_LEVEL` dùng giá trị mặc định trong `app/config.py` (10 request/phút, 1.0 USD/ngày, 10.0 USD/tháng, `INFO`).

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
