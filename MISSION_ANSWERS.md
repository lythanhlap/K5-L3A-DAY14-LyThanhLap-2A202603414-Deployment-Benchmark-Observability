# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 11, Python 3.13 (venv), Docker Desktop 29.9.2 / Compose v5.6.1.


---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code | Hậu quả |
|---|---|---|---|
| 1 | **Hardcode secret** | `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` (dòng 17) và `DATABASE_URL = "postgresql://admin:password123@..."` (dòng 18) | Push lên GitHub là lộ key/mật khẩu DB; muốn đổi key phải sửa code và deploy lại |
| 2 | **Log ra secret** | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` (dòng 34) | Key nằm trong log của platform, ai đọc được log là có key. Đã thấy thật khi chạy: `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this` |
| 3 | **Dùng `print()` thay cho logging có cấu trúc** | dòng 33, 34, 38 | Không có level, không có timestamp, không parse được bằng log aggregator (Loki, Datadog…) |
| 4 | **Không có health check** | không có `/health` (gọi thử trả `404 Not Found`) | Platform không biết agent còn sống hay đã treo để restart |
| 5 | **Bind `localhost`** | `host="localhost"` (dòng 51) | Trong container, `localhost` chỉ là chính container → bên ngoài không gọi vào được |
| 6 | **Port cố định** | `port=8000` (dòng 52) | Railway/Render inject `PORT` qua env var, app không đọc → deploy fail |
| 7 | **Bật reload/debug cứng** | `reload=True` (dòng 53), `DEBUG = True` (dòng 21) | Tốn tài nguyên theo dõi file, lộ thông tin debug trong production |
| 8 | **Config không lấy từ env** | `MAX_TOKENS = 500`, `DEBUG = True` hardcode | Không đổi được giữa dev/staging/prod mà không sửa code |
| 9 | **Không xử lý SIGTERM / graceful shutdown** | không có handler, không có lifespan | Khi platform tắt container, request đang xử lý bị cắt ngang |
| 10 | **Input nhận qua query string** | `def ask_agent(question: str)` | Câu hỏi dài/có ký tự đặc biệt dễ lỗi; câu hỏi lộ trong access log (URL) |

### Exercise 1.2: Chạy bản develop

```text
$ cd 01-localhost-vs-production/develop && python app.py
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Content
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
```

Log server (chứng minh secret bị in ra log):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
INFO:     127.0.0.1:2859 - "POST /ask?question=Hello HTTP/1.1" 200 OK
```

**Quan sát:** app chạy được nhưng chưa sẵn sàng cho production — lộ secret trong log, không có `/health`, chỉ nghe trên `localhost`.

### Exercise 1.3: So sánh develop vs production

Chạy bản production (`PORT=8000 python app.py`):

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:51:36.983908+00:00"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":""}'
HTTP/1.1 422 Unprocessable Content
{"detail":"question field is required"}
```

Server log: `Uvicorn running on http://0.0.0.0:8000` (bind mọi interface, port lấy từ env `PORT`).

| Feature | Develop (basic) | Production (advanced) | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`DEBUG = True`, `MAX_TOKENS = 500`) | `config.py` đọc env (`os.getenv("PORT")`, `LLM_MODEL`…), có `.env.example` | Cùng một image chạy được ở dev/staging/prod chỉ bằng cách đổi env (12-factor "Config"); không phải sửa code để đổi cấu hình |
| Secrets | `OPENAI_API_KEY = "sk-..."` trong code, còn in ra log | Đọc từ env, không log secret; `validate()` bắt buộc có `AGENT_API_KEY` khi `ENVIRONMENT=production` | Secret trong code sẽ nằm vĩnh viễn trong lịch sử git; fail-fast giúp phát hiện thiếu cấu hình ngay lúc khởi động thay vì lúc có user |
| Host / Port | `localhost:8000` cố định | `0.0.0.0` + `PORT` từ env | Container và cloud platform cần bind `0.0.0.0` và nghe đúng port được inject |
| Health check | Không có (`/health` → 404) | `GET /health` (liveness) + `GET /ready` (readiness) | Platform dùng `/health` để restart khi treo, load balancer dùng `/ready` để chỉ gửi traffic vào instance đã sẵn sàng |
| Logging | `print()` | `logging` với format JSON, chỉ log metadata (`question_length`, `client_ip`) | Log JSON dễ parse/search/alert; không log nội dung nhạy cảm |
| Shutdown | Tắt đột ngột | `lifespan` (startup/shutdown) + handler SIGTERM, set `is_ready=False` trước khi tắt | Request đang chạy được hoàn thành, LB ngừng gửi traffic mới → không mất request khi deploy/scale |
| Debug/Reload | `reload=True` luôn bật | `reload=settings.debug` (chỉ bật khi `DEBUG=true`) | Reload tốn tài nguyên và không an toàn trong production |
| Validation input | Query param, thiếu → 422 kiểu FastAPI | Body JSON, câu rỗng → 422 với message rõ ràng | Lỗi rõ ràng cho client, không lộ chi tiết nội bộ |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được API từ trình duyệt |

**Quan sát thêm (lỗi nhỏ của bản production mẫu):** khi chạy, các dòng log JSON của app (`agent_request`, `startup`…) **không xuất hiện**, chỉ thấy `WARNING:root:OPENAI_API_KEY not set — using mock LLM`. Nguyên nhân: `config.py` gọi `logging.warning()` lúc import → Python tự cấu hình root logger (level WARNING, format mặc định) → `logging.basicConfig(...)` trong `app.py` chạy sau trở thành no-op. Cách sửa: gọi `basicConfig(..., force=True)` hoặc cấu hình logging trước khi import `config`. Bài học: structured logging phải được kiểm tra bằng cách chạy thật, không chỉ đọc code. Ngoài ra `.env` được copy nhưng `config.py` không gọi `load_dotenv()`, nên giá trị trong `.env` không có tác dụng nếu không `export` ra env.

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Python đầy đủ trên Debian (có compiler, header, nhiều tool), nên rất nặng (~1 GB).
2. **Working directory:** `/app` (`WORKDIR /app`) — mọi lệnh `COPY`/`RUN`/`CMD` sau đó chạy trong thư mục này.
3. **Vì sao `COPY requirements.txt` trước rồi mới `COPY app.py`:** Docker cache theo từng layer; một layer bị build lại khi input của nó thay đổi, và mọi layer phía sau cũng bị build lại. `requirements.txt` ít thay đổi còn code thay đổi liên tục → copy requirements + `pip install` trước để layer cài dependencies được cache; sửa code chỉ build lại bước `COPY app.py` (vài giây) thay vì cài lại toàn bộ package (vài phút).
4. **CMD vs ENTRYPOINT:** `CMD ["python", "app.py"]` là lệnh mặc định, bị **thay thế hoàn toàn** nếu truyền lệnh khi `docker run image <cmd>`. `ENTRYPOINT` là executable cố định của container, tham số khi `docker run` được **nối thêm** vào sau (muốn đổi phải dùng `--entrypoint`). Hay dùng kết hợp: `ENTRYPOINT` = chương trình, `CMD` = tham số mặc định. Nên dùng exec form (`["..."]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và chạy image develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .     # build từ gốc repo
$ docker run -d -p 8000:8000 my-agent:develop

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.5,"container":true}

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ docker exec <container> sh -c 'whoami; python --version'
root
Python 3.11.18
```

→ Container develop chạy bằng user **root**.

### Exercise 2.3: Multi-stage build — kích thước image

**Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không được deploy.

**Stage 2 (`runtime`, `python:3.11-slim`):** bắt đầu lại từ image slim sạch, tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (packages đã cài, 40.8 MB theo `docker history`) + `main.py` + `utils/mock_llm.py`, thêm `HEALTHCHECK`, chạy uvicorn 2 workers.

**Tại sao nhỏ hơn:** (1) base `slim` thay cho bản full; (2) compiler `gcc`, header `libpq-dev`, cache apt chỉ nằm ở stage builder và bị bỏ đi — image cuối không có các layer đó; (3) chỉ copy đúng những file cần để chạy.

Kết quả đo bằng `docker images` / `docker image inspect`:

```text
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   8403008d5def        240MB         57.4MB
my-agent:develop    d2aacb07c152       1.67GB          426MB

$ docker image inspect my-agent:develop  --format '{{.Size}}'   → 1667775682 bytes
$ docker image inspect my-agent:advanced --format '{{.Size}}'   → 239815343 bytes
```

- Develop: **1667.8 MB** (≈ 1.67 GB)
- Production (advanced): **239.8 MB**
- Chênh lệch: (1667.8 − 239.8) / 1667.8 = **giảm 85.6%** (theo content size nén: 426 MB → 57.4 MB, giảm 86.5%)

Kiểm tra container advanced:

```text
$ docker run -d -p 8000:8000 my-agent:advanced
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"version":"2.0.0","timestamp":"2026-11-25T08:55:41.071708"}
$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"What is Docker?"}'
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ docker exec <container> whoami
appuser
$ docker ps   →   Up 9 seconds (healthy)
```

→ Chạy bằng user non-root `appuser`, Docker `HEALTHCHECK` báo `healthy`, không còn `gcc` trong image.

### Exercise 2.4: Docker Compose stack

Chạy (đã `touch .env.local` trong `02-docker/production/`):

```text
$ docker compose -p k5-day14-docker up -d --build
SERVICE   IMAGE                   STATUS                     PORTS
agent     k5-day14-docker-agent   Up (health: starting)      8000/tcp
nginx     nginx:alpine            Up                         0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up (healthy)               6333-6334/tcp
redis     redis:7-alpine          Up (healthy)               6379/tcp
```

**Sơ đồ kiến trúc:**

```text
                 host :80 / :443
Client ───────────────▶ ┌──────────────────┐
                        │  nginx (alpine)  │  reverse proxy, rate limit 10r/s/IP,
                        │                  │  security headers
                        └────────┬─────────┘
                                 │ proxy_pass http://agent:8000   (network "internal")
                                 ▼
                        ┌──────────────────┐
                        │ agent (FastAPI)  │  không publish port ra host
                        │ uvicorn x2 worker│
                        └───┬──────────┬───┘
            REDIS_URL       │          │   QDRANT_URL
       redis://redis:6379/0 ▼          ▼   http://qdrant:6333
                  ┌──────────────┐  ┌──────────────┐
                  │ redis:7      │  │ qdrant v1.9  │
                  │ (session,    │  │ (vector DB   │
                  │  rate limit) │  │  cho RAG)    │
                  └──────────────┘  └──────────────┘
                   volume redis_data  volume qdrant_data
```

**Các service giao tiếp thế nào:** tất cả nằm trong bridge network `internal`, gọi nhau bằng **tên service** qua Docker DNS (`agent`, `redis`, `qdrant`). Chỉ `nginx` publish port ra host; `agent` không expose port nên mọi traffic phải đi qua Nginx. `depends_on: condition: service_healthy` bảo đảm agent chỉ start sau khi Redis và Qdrant healthy; Nginx start sau agent.

**Test qua Nginx:** trên máy này cổng 80 của host đã bị Apache (XAMPP) chiếm nên `curl http://localhost/health` trả trang 404 của Apache. Vì vậy test bằng một container curl trong cùng network `k5-day14-docker_internal` (gọi vào service `nginx`):

```text
$ docker run --rm --network k5-day14-docker_internal curlimages/curl:8.11.1 -s -i http://nginx/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":21.5,"version":"2.0.0","timestamp":"2026-11-25T08:56:55.878375"}

$ docker run --rm --network k5-day14-docker_internal curlimages/curl:8.11.1 -s -i -X POST http://nginx/ask \
    -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -s -m 3 localhost:8000/health   → không kết nối được (agent không publish port, đúng thiết kế)
```

Response có header `Server: nginx` và các security header do Nginx thêm → request thực sự đi qua Nginx rồi mới tới agent. Dọn: `docker compose -p k5-day14-docker down -v`.

Ghi chú: `main.py` của stack này chưa thực sự dùng Redis/Qdrant (chỉ nhận `REDIS_URL`/`QDRANT_URL`), compose dùng để minh hoạ orchestration nhiều service.

---

## Part 3: Cloud Deployment

> Theo CHECKPOINTS của lớp K5, Part 3 được làm **sau Part 6** và deploy chính agent trong `06-lab-complete/` (CP6).

### Exercise 3.1 / 3.2: Deploy

- Nền tảng chính: _(điền sau CP6 — Railway hoặc Render)_
- Public URL: _(điền sau CP6)_
- Kết quả 4 lệnh tự kiểm ở CP6 (`/health` 200, `/ready` 200, `/ask` không key 401, có key 200): _(điền sau CP6)_
- Screenshot: `screenshots/dashboard.png`, `screenshots/running.png`, `screenshots/test.png`

### So sánh `railway.toml` và `render.yaml` (trong `03-cloud-deployment/`)

| Điểm so sánh | `railway/railway.toml` | `render/render.yaml` |
|---|---|---|
| Định dạng / phạm vi | TOML, mô tả **một service** (build + deploy) | YAML Blueprint, mô tả **nhiều service** trong một file (web + Redis) |
| Build | `builder = "NIXPACKS"` — Railway tự nhận diện ngôn ngữ (hoặc dùng Dockerfile nếu đổi builder) | `runtime: python` + `buildCommand: pip install -r requirements.txt` khai báo tường minh |
| Start | `startCommand = "uvicorn app:app --host 0.0.0.0 --port $PORT"` | `startCommand: uvicorn app:app --host 0.0.0.0 --port $PORT` (giống nhau: đều đọc `$PORT` do platform inject) |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 30` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, tối đa 3 lần | Render tự quản lý, không khai báo trong file |
| Biến môi trường / secret | **Không** khai báo trong file; đặt bằng `railway variables set ...` hoặc dashboard | Khai báo trong `envVars`: giá trị thường ghi thẳng (`ENVIRONMENT=production`), secret dùng `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` (Render tự sinh `AGENT_API_KEY`) |
| Hạ tầng đi kèm | Redis phải tạo riêng (thêm plugin/service trong project) rồi trỏ `REDIS_URL` | Khai báo luôn `type: redis` (`agent-cache`) trong cùng file — Infrastructure as Code |
| Region / plan | Chọn trên dashboard | Ghi trong file: `region: singapore`, `plan: free` |
| Cách deploy | CLI `railway up` đẩy code từ máy lên | Git-based: push GitHub → Blueprint đọc file, `autoDeploy: true` deploy mỗi lần push |

**Vì sao khác nhau:** Railway thiên về trải nghiệm CLI nhanh, cấu hình tối thiểu, secret quản lý ngoài file. Render coi `render.yaml` là IaC cho cả hệ thống (service + database + biến môi trường) và gắn với GitHub, nên khai báo nhiều hơn nhưng tái tạo được toàn bộ hạ tầng từ repo. Cả hai đều tuân 12-factor: secret không commit giá trị thật, port lấy từ `$PORT`, có health check.

### Exercise 3.3: Pipeline CI/CD trên Cloud Run (đọc `cloudbuild.yaml` + `service.yaml`)

1. **Trigger:** push lên nhánh `main` kích hoạt Cloud Build chạy `cloudbuild.yaml` (máy `E2_MEDIUM`, timeout 20 phút).
2. **Test:** bước `test` chạy trong `python:3.11-slim`, cài `requirements.txt` + `pytest` và chạy `pytest tests/`; test fail thì pipeline dừng, không build. (Lưu ý: thư mục mẫu chưa có `tests/`, cần thêm test thì bước này mới pass.)
3. **Build → Push:** build Docker image gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest` (dùng `--cache-from :latest` để tái dùng layer cache), rồi push lên Container Registry. Các bước nối nhau bằng `waitFor`.
4. **Deploy:** `gcloud run deploy ai-agent` với image tag `$COMMIT_SHA` (truy vết và rollback được theo commit), region `asia-southeast1`, `min-instances=1` (tránh cold start), `max-instances=10`, 512Mi / 1 CPU, timeout 60s, secret `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`) thay vì ghi thẳng.
5. **`service.yaml`** là bản khai báo (IaC) tương ứng: Knative Service với autoscale 1–10 instance, `containerConcurrency: 80`, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, `AGENT_API_KEY` và `OPENAI_API_KEY` lấy từ `secretKeyRef`; áp dụng bằng `gcloud run services replace service.yaml`.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/`)

- **Key được check ở đâu?** Trong dependency `verify_api_key()` (dùng `APIKeyHeader(name="X-API-Key", auto_error=False)`), được gắn vào endpoint bằng `_key: str = Depends(verify_api_key)` ở `POST /ask`. `/` và `/health` vẫn public.
- **Sai key thì sao?** Thiếu header → **401** `"Missing API key..."`; có header nhưng sai → **403** `"Invalid API key."`. (Lưu ý: hợp đồng của Part 6 yêu cầu sai key cũng phải **401**, khác với bản mẫu này.)
- **Rotate key thế nào?** Key đọc từ env `AGENT_API_KEY` → đổi giá trị env trên platform rồi restart/redeploy, không sửa code. Để rotate không downtime: cho phép **nhiều key hợp lệ cùng lúc** (vd `AGENT_API_KEYS=old,new`), phát key mới cho client, sau khi client chuyển hết thì xoá key cũ.

Chạy: `AGENT_API_KEY=secret-key-123 python app.py`

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/`)

**JWT flow (`auth.py`):**
1. Client `POST /auth/token` với username/password → `authenticate_user()` kiểm tra trong `DEMO_USERS` (sai → 401 `Invalid credentials`).
2. `create_token()` ký payload `{sub, role, iat, exp = now + 60 phút}` bằng HS256 với `JWT_SECRET` (đọc từ env).
3. Client gửi `Authorization: Bearer <token>` ở mỗi request. `verify_token()` kiểm tra chữ ký + hạn dùng, trả `{username, role}` — **không cần tra database** (stateless). Thiếu token → 401, hết hạn → 401, token sai chữ ký → 403.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJhbGciO...<masked>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJhbGciO...<masked>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer invalid.token.here" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** **Sliding window log** — mỗi user có một `deque` timestamp; mỗi request xoá các timestamp cũ hơn 60 giây, đếm số còn lại, đủ giới hạn thì trả 429 (kèm `Retry-After` tính từ request cũ nhất), chưa đủ thì thêm timestamp hiện tại.
- **Giới hạn:** user thường (`student`, role `user`) **10 request / 60 giây**; admin (`teacher`, role `admin`) **100 request / 60 giây**.
- **Bypass cho admin:** không bỏ qua hẳn mà dùng limiter riêng theo role: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`. Muốn bypass hoàn toàn thì bỏ qua `limiter.check()` khi `role == "admin"`.
- **Hạn chế:** state nằm trong RAM của process → mất khi restart và không chia sẻ giữa nhiều instance/worker; production cần chuyển sang Redis (sorted set) như Part 6.

Vòng lặp 20 request với cùng token (request 1 ở Exercise 4.2 đã dùng 1 lượt):

```text
Request  1: HTTP/1.1 200 OK  {"question":"Test 1", ... "usage":{"requests_remaining":8, ...}}
Request  2: HTTP/1.1 200 OK
Request  3: HTTP/1.1 200 OK
Request  4: HTTP/1.1 200 OK
Request  5: HTTP/1.1 200 OK  {... "requests_remaining":4 ...}
Request  6: HTTP/1.1 200 OK
Request  7: HTTP/1.1 200 OK
Request  8: HTTP/1.1 200 OK  {... "requests_remaining":1 ...}
Request  9: HTTP/1.1 200 OK
Request 10: HTTP/1.1 429 Too Many Requests  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":46}}
Request 11: HTTP/1.1 429 Too Many Requests  {"detail":{... "retry_after_seconds":45}}
...
Request 20: HTTP/1.1 429 Too Many Requests  {"detail":{... "retry_after_seconds":40}}
```

Response đầy đủ của lần 429 đầu tiên:

```text
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795597145
retry-after: 46
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":46}}
```

→ Đúng 10 request thành công trong 60 giây, request thứ 11 trở đi bị chặn 429, `Retry-After` giảm dần theo thời gian.

### Exercise 4.4: Cost guard

**Bản mẫu (`cost_guard.py`) hoạt động thế nào:**
- Mỗi user có `UsageRecord` theo ngày (`input_tokens`, `output_tokens`, `request_count`); cost = `input/1000 × $0.00015 + output/1000 × $0.0006` (giá GPT-4o-mini).
- `check_budget()` chạy **trước** khi gọi LLM: tổng toàn hệ thống ≥ $10/ngày → **503**; user ≥ $1/ngày → **402 Payment Required**; ≥ 80% → log warning.
- `record_usage()` chạy **sau** khi gọi LLM để cộng chi phí thật. Sang ngày mới thì record tự reset.

Demo 402 bằng budget rất nhỏ ($0.0001):

```text
User student at 81% budget
call 1: OK  cost so far = $2.7e-05
call 2: OK  cost so far = $5.4e-05
call 3: OK  cost so far = $8.1e-05
call 4: OK  cost so far = $0.000108
call 5: HTTP 402 {'error': 'Daily budget exceeded', 'used_usd': 0.000108, 'budget_usd': 0.0001, 'resets_at': 'midnight UTC'}
```

`GET /me/usage` sau 10 request: `{"requests":10,"input_tokens":40,"output_tokens":300,"cost_usd":0.000186,"budget_usd":1.0,"budget_remaining_usd":0.999814,...}`.

**Cách implement cost guard theo yêu cầu đề ($10/tháng/user, lưu Redis, reset đầu tháng):**

```python
import os
from datetime import datetime, timezone
import redis

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month}"          # key theo tháng → sang tháng mới tự là key mới (reset)
    current = float(r.get(key) or 0)
    if current + estimated_cost > MONTHLY_BUDGET_USD:
        return False                            # endpoint trả HTTP 402
    r.incrbyfloat(key, estimated_cost)          # atomic, an toàn khi nhiều instance cùng ghi
    r.expire(key, 32 * 24 * 3600)               # tự dọn key cũ sau ~1 tháng
    return True
```

Ý tưởng chính: (1) lưu trong **Redis** chứ không trong RAM, để mọi instance/worker thấy chung một con số và không mất khi restart; (2) **key theo tháng** nên không cần job reset; (3) `INCRBYFLOAT` là lệnh atomic, tránh race condition khi nhiều request đồng thời; (4) kiểm tra **trước** khi gọi LLM và trả **402**, có thể ghi lại cost thật sau khi gọi xong.

**Vì sao cần cả rate limit lẫn cost guard:** rate limit bảo vệ **tốc độ** (chống spam/DoS, chia đều tài nguyên), còn cost guard bảo vệ **tiền**. Một user gửi đúng 10 request/phút nhưng mỗi request là prompt rất dài vẫn có thể đốt hàng trăm đô một tháng — rate limit không chặn được, chỉ cost guard chặn được.

**Lỗi nhỏ phát hiện trong bản mẫu:** field `budget_remaining_usd` trong response `/ask` thực ra trả về `usage.total_cost_usd` (số **đã dùng**, `1.9e-05`), không phải số còn lại — đúng ra phải là `daily_budget_usd - total_cost_usd` như ở `/me/usage`.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/`)

- **`/health` — liveness:** "process còn sống không?". Trả 200 kèm uptime, version, environment, kiểm tra memory (psutil). Platform dùng nó để **restart** container khi fail/timeout. Không nên phụ thuộc vào dependency bên ngoài, vì Redis chết mà restart agent cũng không chữa được.
- **`/ready` — readiness:** "đã sẵn sàng nhận traffic chưa?". Trả **503** khi đang khởi động, đang shutdown (`_is_ready=False`) hoặc dependency (Redis/DB) chưa kết nối. Load balancer dùng nó để **ngừng gửi traffic** vào instance đó (không restart).

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:59:05.188805+00:00","checks":{"memory":{"status":"ok","note":"psutil not installed"}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}

$ time curl -s -X POST "http://localhost:8000/ask?question=hello"
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
real 0m3.279s          ← mock LLM delay 3 giây
```

(`in_flight_requests` = 1 vì chính request `/ready` cũng đi qua middleware đếm.)

### Exercise 5.2: Graceful shutdown

**Cơ chế trong code:** `handle_sigterm()` chỉ log; uvicorn tự bắt SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy (`timeout_graceful_shutdown=30`) → chạy phần shutdown của `lifespan` (`_is_ready=False`, chờ `_in_flight_requests == 0` tối đa 30 giây) → thoát.

**Test với SIGTERM thật:** trên Windows không gửi được SIGTERM cho process Python, nên chạy app trong container `python:3.11-slim` (app là PID 1) và dùng `docker stop` (gửi SIGTERM, chờ tối đa 30 giây rồi mới SIGKILL). Gửi request, sau 0.5 giây thì `docker stop`:

```text
Gửi SIGTERM (docker stop) lúc t=0.5s
Request 1: {"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}  -> HTTP 200 sau 3.03s
Exit code container: 0

=== container log
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Shutting down
INFO:     172.17.0.1:45830 - "POST /ask?question=hello HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 09:00:52,120 INFO 🔄 Graceful shutdown initiated...
2026-11-25 09:00:52,120 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1]
2026-11-25 09:00:52,121 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

→ Request đang xử lý **vẫn hoàn thành 200** dù SIGTERM đến giữa chừng, process thoát với **exit code 0**.

**Phát hiện khi gửi 2 request song song** (lặp lại 2 lần, kết quả giống nhau):

| Kịch bản | Request 1 | Request 2 | Exit code |
|---|---|---|---|
| 1 request + SIGTERM | 200 | — | 0 |
| 2 request song song + SIGTERM (code gốc `async def ask_agent`) | 200 | **bị cắt** (curl exit 52 – empty reply) | 0 |
| 2 request song song + SIGTERM (bản sao đổi thành `def ask_agent`) | 200 | **200** | 0 |

**Giải thích:** `ask_agent` khai báo `async def` nhưng bên trong gọi `ask()` dùng `time.sleep(3)` **đồng bộ** → chặn event loop. Request 2 đã tới socket nhưng event loop bị chặn nên chưa kịp được đọc/xử lý; khi request 1 xong, uvicorn đang trong trạng thái shutdown và đóng các kết nối chưa có request đang chạy → request 2 mất. Khi đổi thành `def` (FastAPI chạy trong threadpool), cả 2 request chạy song song và đều hoàn thành trước khi tắt, log có dòng `Waiting for connections to close`. Bài học: graceful shutdown chỉ đúng khi code không chặn event loop — trong `async def` phải dùng thư viện async (`await asyncio.sleep`, client HTTP async), còn code blocking thì dùng `def` hoặc `run_in_threadpool`.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- **Anti-pattern:** lưu `conversation_history = {}` trong RAM → mỗi instance (và mỗi uvicorn worker) có bộ nhớ riêng. Khi load balancer gửi request 2 của user sang instance khác thì history "biến mất"; restart/deploy hay scale down cũng mất hết state.
- **Cách làm trong code:** mọi session lưu ở Redis với key `session:{session_id}` (`setex`, TTL 3600 giây, giữ tối đa 20 message). `append_to_history()` = `load_session` → thêm message → `save_session`. Instance chỉ giữ `INSTANCE_ID` (để quan sát), không giữ dữ liệu người dùng → bất kỳ instance nào cũng phục vụ được bất kỳ user nào, có thể scale ngang và kill instance tuỳ ý.
- Code có fallback in-memory nếu không kết nối được Redis (in cảnh báo `not scalable!`) — tiện khi dev local nhưng trong production nên fail ở `/ready` thay vì âm thầm chuyển sang RAM.
- **Rủi ro còn lại:** `append_to_history` là read-modify-write không atomic; nếu 2 request cùng session tới 2 instance cùng lúc có thể ghi đè nhau. Cách tốt hơn: dùng Redis list (`RPUSH` + `LTRIM`) — atomic.

### Exercise 5.4: Load balancing với Nginx

```text
$ cd 05-scaling-reliability/production && touch .env.local
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up (health: starting)     8000/tcp
k5-day14-scaling-agent-2   Up (health: starting)     8000/tcp
k5-day14-scaling-agent-3   Up (health: starting)     8000/tcp
k5-day14-scaling-nginx-1   Up                        0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up (healthy)              6379/tcp
```

10 request `POST http://localhost:8080/chat`:

```text
Request  1: HTTP 200  X-Served-By=172.22.0.3:8000  served_by=instance-ce1551  storage=redis
Request  2: HTTP 200  X-Served-By=172.22.0.5:8000  served_by=instance-ad8b5e  storage=redis
Request  3: HTTP 200  X-Served-By=172.22.0.4:8000  served_by=instance-c13307  storage=redis
Request  4: HTTP 200  X-Served-By=172.22.0.3:8000  served_by=instance-ce1551  storage=redis
Request  5: HTTP 200  X-Served-By=172.22.0.5:8000  served_by=instance-ad8b5e  storage=redis
Request  6: HTTP 200  X-Served-By=172.22.0.4:8000  served_by=instance-c13307  storage=redis
Request  7: HTTP 200  X-Served-By=172.22.0.3:8000  served_by=instance-ce1551  storage=redis
Request  8: HTTP 200  X-Served-By=172.22.0.5:8000  served_by=instance-ad8b5e  storage=redis
Request  9: HTTP 200  X-Served-By=172.22.0.4:8000  served_by=instance-c13307  storage=redis
Request 10: HTTP 200  X-Served-By=172.22.0.3:8000  served_by=instance-ce1551  storage=redis
Phân bố: {'instance-ce1551': 4, 'instance-ad8b5e': 3, 'instance-c13307': 3}
```

`docker compose -p k5-day14-scaling logs agent` cũng cho thấy `POST /chat` xuất hiện ở cả `agent-1`, `agent-2`, `agent-3`.

**Giải thích:** Nginx `upstream agent_cluster { server agent:8000; }` — Docker DNS trả về IP của cả 3 container cho tên `agent`, Nginx phân phối **round-robin** (thấy rõ chu kỳ .3 → .5 → .4). Header `X-Served-By` (Nginx thêm bằng `$upstream_addr`) và field `served_by` (agent trả về) khớp nhau. `proxy_next_upstream error timeout http_503` + `proxy_next_upstream_tries 3` giúp tự chuyển sang instance khác nếu một instance lỗi.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
============================================================
Stateless Scaling Demo
============================================================

Session ID: 14fe04e9-5204-434b-a883-1acdcc4f267d

Request 1: [instance-ce1551]
  Q: What is Docker?
Request 2: [instance-ad8b5e]
  Q: Why do we need containers?
Request 3: [instance-c13307]
  Q: What is Kubernetes?
Request 4: [instance-ce1551]
  Q: How does load balancing work?
Request 5: [instance-ad8b5e]
  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-ad8b5e', 'instance-ce1551', 'instance-c13307'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ... (đủ 5 cặp user/assistant)

✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu hỏi trong **cùng một session** được 3 instance khác nhau xử lý, nhưng history vẫn đủ 10 message (5 user + 5 assistant) đúng thứ tự, vì mỗi instance đọc/ghi chung key `session:<id>` trong Redis. Nếu lưu trong RAM, mỗi instance chỉ thấy phần history của chính nó.

**Thử thêm — tắt 1 instance giữa cuộc hội thoại** (script không tự kill nên làm thủ công):

```text
Bước 1: tạo session, 3 turn
  turn 1: HTTP 200 served_by=instance-c13307
  turn 2: HTTP 200 served_by=instance-ce1551
  turn 3: HTTP 200 served_by=instance-ad8b5e
Bước 2: docker stop k5-day14-scaling-agent-2        (instance-c13307)
  k5-day14-scaling-agent-1 Up (healthy)
  k5-day14-scaling-agent-3 Up (healthy)
Bước 3: tiếp tục cùng session
  turn 4: HTTP 200 served_by=instance-ce1551
  turn 5: HTTP 200 served_by=instance-ad8b5e
  turn 6: HTTP 200 served_by=instance-ce1551
  history: 12 messages, user turns = ['Turn 1 ...', 'Turn 2 ...', 'Turn 3 ...', 'Turn 4 ...', 'Turn 5 ...', 'Turn 6 ...']
```

→ Instance đã xử lý turn 1 bị tắt, nhưng các turn sau vẫn 200 (Nginx chuyển sang 2 instance còn lại) và history giữ đủ 6 turn. Đây chính là lợi ích của stateless: instance có thể chết/thay thế bất kỳ lúc nào mà không mất dữ liệu người dùng.

Ghi chú nhỏ: field `turn` trong response bị lệch +1 (turn đầu trả `2`) vì code đếm số câu user **sau** khi đã append câu hỏi hiện tại rồi lại `+ 1`.

Dọn: `docker compose -p k5-day14-scaling down -v`.

---

## Part 6: Final Project

> Điền sau CP5: dán phần "📋 Checklist Deliverable" từ output `python check_production_ready.py` (chạy đầy đủ, có Docker) trong `06-lab-complete/`.

Chạy bản đầy đủ (có Docker) từ WSL Ubuntu, vì trên Windows script không gửi được SIGTERM nên luôn để mục 11 ở ⚠️:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

---

## AI log

| # | Hỏi / nhờ AI làm gì | AI trả lại gì | Tôi tự kiểm tra thế nào |
|---|---|---|---|
| 1 | Dùng Claude Code (Claude Opus 5.5) đọc toàn bộ file `.md` của đề và tóm tắt hướng dẫn làm lab, các điểm K5 khác `CODE_LAB.md` | Bản tóm tắt lộ trình CP0–CP8, hợp đồng Part 6, cấu trúc nộp | Đối chiếu lại với `README.md`, `CHECKPOINTS.md`, `RUBRIC.md`, `SUBMISSION.md` |
| 2 | Nhờ Claude Code chạy các ví dụ Part 1, 2, 4, 5 trên máy (Python venv + Docker Desktop) và soạn nháp câu trả lời Part 1–5 | Các lệnh đã chạy, output thật (mã HTTP, kích thước image, log container) và bản nháp file này | _(tự điền: đã đọc lại từng câu trả lời, chạy lại lệnh nào, sửa gì)_ |
| 3 | Hỏi vì sao request thứ 2 bị cắt khi SIGTERM ở Exercise 5.2 | Giả thuyết: `async def` + `time.sleep` chặn event loop; kiểm chứng bằng bản sao đổi sang `def` thì cả 2 request đều 200 | _(tự điền)_ |
