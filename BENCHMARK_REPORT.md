# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ file report thật: `reports/*.json` (load test, eval), `benchmark-results/*.csv` (Locust), screenshot trong `screenshots/` và query Prometheus/Jaeger lúc chạy.

**Họ tên:** Lý Thanh Lập
**Ngày chạy:** 25/11/2026
**Môi trường:** Laptop Windows 11, AMD Ryzen 5 5500U (6 nhân / 12 luồng), RAM 15.3 GB; Docker Desktop 29.9.2 (VM 12 CPU / ~7.4 GB). Bước 1 chạy agent bằng Python 3.13 trên Windows (1 process uvicorn mỗi agent, load generator chạy cùng máy). Bước 2 chạy bằng Docker Compose, mỗi container agent giới hạn 0.5 CPU / 256 MB.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Một request bình thường mất ~200 ms (mock LLM 120 ms + jitter + retrieval 20 ms), 500 ms là khoảng 2.5 lần mức bình thường: đủ chỗ cho jitter và tải vừa, nhưng vẫn bắt được khi hệ thống bắt đầu xếp hàng. Đây cũng là ngưỡng mặc định của `load_test.py`. Alert `AgentHighLatencyP95` đặt lỏng hơn (> 1 s trong 2 phút) để tránh báo động vì spike ngắn. |
| Error rate `/ask` | < 2 % | Upstream LLM (mock) tự lỗi ~1 %, nên SLO 2 % chừa error budget gấp đôi mức lỗi nền. Alert `AgentHighErrorRate` đặt > 5 % trong 1 phút: chỉ báo khi lỗi vượt rõ rệt và kéo dài. |
| Accuracy trên golden dataset | ≥ 85 % | Golden dataset 23 câu: 85 % nghĩa là sai tối đa 3 câu. Thấp hơn mức này agent trả lời sai quá nhiều để dùng. Quality gate trong CI còn yêu cầu **không có regression** so với baseline, nên dù accuracy vẫn ≥ 85 %, làm hỏng một câu đang đúng cũng bị chặn. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh: `python load_test.py --url http://127.0.0.1:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (agent v2, `MOCK_SLOW_RATE` mặc định 0.03).

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.22 | 207.9 | 239.4 | 1703.5 | 1.33 % | ✅ PASS |
| 10 | 36.38 | 205.7 | 239.4 | 1705.3 | 0.67 % | ✅ PASS |
| 50 | 93.48 | 214.4 | 1685.5 | 1746.3 | 0.33 % | ❌ FAIL (p95) |
| 100 | 58.09 | 655.1 | 3933.4 | 4304.4 | 2.33 % | ❌ FAIL (p95, error rate) |

File: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** RPS **không** tăng tuyến tính theo concurrency.

- **Từ 1 lên 10:** RPS tăng gần tuyến tính (4.22 → 36.38, gấp 8.6 lần). Agent là async và thời gian chủ yếu là *chờ* LLM (I/O), nên 10 request chạy song song gần như không tranh nhau. Theo Little's law, RPS ≈ concurrency / latency: 10 / 0.253 s ≈ 39.5 req/s, khớp với số đo được.
- **Lên 50:** RPS chỉ tăng 2.6 lần (93.48), không phải 5 lần. Mỗi request vẫn tốn CPU cho JSON, metrics, log, mà agent chỉ có **1 process uvicorn**, load generator lại chạy cùng máy. Ngoài ra lượt chạy chỉ dài 3.2 s, nên vài request chậm 1.7 s (tail) chiếm phần lớn thời gian chạy. p95 nhảy lên 1685.5 ms: với 300 request, 3 % chậm (~9 request) đã chiếm hết phần trên p95 khi các request bình thường kết thúc gần như cùng lúc.
- **Lên 100:** RPS **giảm** còn 58.09 và p50 tăng lên 655.1 ms. Process bão hoà CPU nên request phải **xếp hàng** trong event loop, latency của mọi request đều tăng (không chỉ tail), throughput đi xuống. Error rate 2.33 % (7/300) vẫn là lỗi 503 do mock LLM tự sinh (~1 %), không phải lỗi quá tải; với 300 mẫu, dao động 1–2 % là bình thường.

Kết luận: điểm "đầu gối" nằm giữa 10 và 50 concurrency. Muốn chịu tải cao hơn phải thêm process hoặc instance (scale ngang), không thể chỉ tăng concurrency.

### 2.2 Tail latency

Mỗi lần khởi động lại v2 với `MOCK_SLOW_RATE` tương ứng, chạy `--requests 300 --concurrency 20`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 207.0 | 239.4 | 263.0 |
| 0.03 (mặc định) | 251.6 | 237.6 | 1697.7 |
| 0.1 | 339.0 | 1696.4 | 1731.2 |

File: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:** **p99 nhạy nhất** với request chậm. Chỉ cần 3 % request chậm là p99 nhảy từ 263 ms lên 1697.7 ms (gấp 6.5 lần), trong khi avg chỉ tăng 44.6 ms (+21 %) và p95 gần như không đổi. Khi tỉ lệ chậm lên 10 % (vượt 5 %), cả **p95** cũng nhảy lên 1696.4 ms, nhưng avg vẫn chỉ 339 ms.

SLO không nên đặt theo average vì average **che giấu** nhóm người dùng bị chậm. Với slow rate 0.1, avg 339 ms trông vẫn ổn (dưới 500 ms), nhưng thực tế cứ 10 người thì 1 người chờ ~1.7 s. Percentile mô tả trải nghiệm của X % người dùng tệ nhất, nên SLO đặt theo p95/p99 mới phản ánh đúng chất lượng dịch vụ.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.35 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 56.9 | 109.1 | 113.9 |
| Tổng cost (USD) | 0.000401 | 0.000762 | 0.000793 |
| Cost / câu đúng (USD) | 0.00040095 | 0.00003627 | 0.00003448 |
| Quality gate | ❌ FAIL (accuracy < 80 %) | ✅ PASS (≥ 80 %, 0 regression so với v1) | ✅ PASS (`--min-accuracy 1.0`, 0 regression so với v2) |

File: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Knowledge base không có tài liệu nào chứa từ "jwt", nên retrieval trả rỗng và agent rơi về câu trả lời chung chung. Đã thêm entry kích hoạt bởi `["jwt"]`: *"API key là chuỗi bí mật cố định, không tự hết hạn. JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn; server chỉ cần xác minh chữ ký, không phải tra database."* |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Tương tự, không có tài liệu về HPA. Đã thêm entry kích hoạt bởi `["hpa", "kubernetes", "autoscal"]`: *"Kubernetes HPA (Horizontal Pod Autoscaler) tự tăng giảm số pod dựa trên CPU, memory hoặc custom metrics như RPS hay độ dài hàng đợi."* |

Sửa trong `develop/utils/mock_llm.py`, chép cả sang `production/utils/mock_llm.py` để stack Docker cũng trả lời đúng. Trước khi thêm, đã kiểm tra các từ khoá mới (`jwt`, `hpa`, `kubernetes`, `autoscal`) chỉ xuất hiện trong đúng 2 câu này của golden dataset, nên không làm retrieval của câu khác đổi tài liệu. Kết quả: `--baseline reports/eval_v2.json` báo **cải thiện 2 câu, regression 0**.

**Quality gate trong CI:** `run_eval.py` trả **exit code 1** khi accuracy dưới `--min-accuracy` hoặc có regression so với `--baseline`. Đã kiểm chứng: chạy eval v1 với `--baseline reports/eval_v2.json` báo **20 câu regression** và exit code **1**; eval v2 (và v2 sau khi sửa) trả exit code **0**. CI chỉ cần chạy lệnh này làm một bước: exit code khác 0 thì job fail và PR không merge được (bật *required status check* cho nhánh `main`):

```yaml
# .github/workflows/eval.yml
name: quality-gate
on: [pull_request]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - name: Run agent + eval
        working-directory: 06-lab-complete/benchmark-observability/develop
        run: |
          pip install -r requirements.txt
          AGENT_VERSION=v2 MOCK_ERROR_RATE=0 python app.py &
          sleep 3
          python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 \
            --baseline reports/eval_v2_fixed.json   # exit 1 → job fail → chặn merge
```

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 có retrieval nên accuracy tăng từ 4.35 % lên 91.3 % (100 % sau khi bổ sung knowledge base).
- **Chi phí:** prompt của v2 chứa thêm tài liệu nên tốn **gấp ~1.9 lần token mỗi câu** (109.1 so với 56.9) và tổng cost gấp ~1.9 lần (0.000762 so với 0.000401 USD). Nhưng tính trên **mỗi câu đúng**, v2 rẻ hơn **~11 lần** (0.0000363 so với 0.000401 USD), vì v1 tốn tiền cho 22 câu trả lời sai.
- **Latency:** v2 chậm hơn một chút. p50 của eval là 202.8 ms so với 157.5 ms, do thêm bước retrieval (~20 ms) và prompt dài hơn (mock cộng 0.2 ms mỗi token input). Mức chênh này nhỏ so với SLO 500 ms.
- **Kết luận:** v2 đáng đồng tiền. So hai phiên bản phải dùng *cost / câu đúng* thay vì *cost / câu*, vì một câu trả lời rẻ nhưng sai không có giá trị.
- Lưu ý: p95 của `eval_v2_fixed` là 1552.4 ms vì 1 trong 23 câu rơi vào nhóm 3 % request chậm của mock. Eval 23 câu quá nhỏ để kết luận về latency; latency phải đo bằng load test.

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build`, tải nền **30 user Locust (ramp 5/s)** trong suốt phần quan sát.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png)

**Exercise 7.4: đọc dashboard khi có tải (2 instance, 30 user).** Cột 1 là số query trực tiếp từ Prometheus API (cửa sổ `rate[1m]`) sau khi tải chạy 155 s; cột 2 là số trên ảnh Grafana chụp ít phút sau đó. Hai cột lệch nhau một chút vì đo ở hai thời điểm khác nhau.

| Chỉ số | Prometheus query | Ảnh Grafana |
|---|---|---|
| Traffic `/ask` | 18.7 req/s | 18.5 req/s |
| Error rate | 0.49 % | 0.88 % |
| p50 / **p95** / p99 | 179.5 ms / **293.7 ms** / 1823.1 ms | p95 293.62 ms (p99 ~1.8 s trên biểu đồ) |
| **avg** | **232.4 ms** | đường `avg` trên biểu đồ latency |
| Req/s theo instance | 9.29 (`172.23.0.2`) và 9.36 (`172.23.0.3`) | hai đường gần như trùng nhau |
| Tokens/s | input 1445.1, output 759.7 | stacked, tổng ~2.2K |
| Instances up | 2 | 2 |

1. **p95 và avg đang là bao nhiêu? Khoảng cách nói lên điều gì?** p95 ≈ 293.7 ms, avg ≈ 232.4 ms, còn p99 lên tới 1823.1 ms. avg chỉ cao hơn p50 khoảng 50 ms, nhưng p99 gấp ~8 lần avg. Phân bố latency có **đuôi dài**: phần lớn request nhanh (~180–290 ms), còn ~3 % request chậm ~1.7 s (mock `MOCK_SLOW_RATE=0.03`). avg bị kéo lên một chút nhưng không cho thấy nhóm người dùng phải chờ gần 2 giây; panel p50/p95/p99 trên Grafana thấy rõ khoảng cách đó.
2. **Nginx có chia đều traffic cho 2 instance không?** Có. Panel "Requests/s theo instance" cho thấy 9.29 và 9.36 req/s, hai đường gần như trùng nhau. Nginx dùng round-robin (`server agent:8000 resolve`) và hai instance có cùng năng lực.
3. **Vì sao input tokens/s lớn hơn output tokens/s?** Input = system prompt + **tài liệu retrieval (context)** + câu hỏi. Agent v2 nhét nguyên đoạn tài liệu vào prompt nên input dài, còn output chỉ là câu trả lời ngắn. Vì vậy input ≈ 1445 tokens/s, gấp ~1.9 lần output ≈ 760 tokens/s. (Panel Tokens/s vẽ dạng stacked, đường trên cùng là tổng ~2.2K.)
4. **`curl http://localhost:8080/metrics` trả về gì? Vì sao?** Trả **HTTP 404** từ Nginx (`nginx/1.28.3`). `nginx.conf` có `location = /metrics { return 404; }`: endpoint metrics không được public ra internet vì lộ thông tin nội bộ (route, lưu lượng, phiên bản, chi phí). Prometheus vẫn đọc được vì nó scrape **trực tiếp** từng container agent trong mạng nội bộ `obs_net` (DNS service discovery), không đi qua Nginx.

Alert rules đã pass unit test: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.2 Thí nghiệm "phá hệ thống"

Đổi cấu hình bằng biến môi trường rồi `docker compose up -d --force-recreate --scale agent=2 agent`; trạng thái alert lấy từ Prometheus API `/api/v1/alerts`, poll mỗi 5 giây. Thời gian tính từ lúc lệnh đổi cấu hình chạy xong.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (giá trị 20.9 %) | **95 s** (pending sau 35 s, firing sau 95 s; `for: 1m`) | Error rate (Golden Signals, chuyển đỏ), Requests/s theo status code (503 tăng), LLM calls theo kết quả (error tăng) |
| B | `MOCK_SLOW_RATE=0.3` | Chưa đo được: Docker Desktop tự khởi động lại giữa thí nghiệm (hết RAM), Prometheus không chạy | — | Dự kiến: Latency p50/p95/p99 (p95/p99 vọt lên ~1.7 s, avg tăng ít hơn) → `AgentHighLatencyP95` sau ≥ 2 phút |
| C | `LLM_MODEL=mock-llm-large` | Chưa đo được (cùng lý do) | — | Dự kiến: Cost burn rate (USD/giờ) tăng ~16 lần, LLM latency p95 tăng ~2.5 lần → `LLMCostBurnRateHigh` |
| D | `docker pause k5-day14-observability-agent-2` (làm khi A vẫn đang bật) | `AgentInstanceDown` | **46 s** (pending sau 16 s, firing sau 46 s; `for: 30s`) | Instances up giảm từ 2 xuống 1; Requests/s theo instance mất 1 đường |
| E | `AGENT_VERSION=v1` | Không có (không chạy được do Docker khởi động lại; theo thiết kế cũng không có alert nào bắt được) | — | Retrieval hit ratio (rơi về 0), Tokens/s (giảm vì prompt không còn context), bảng "Phiên bản đang chạy" đổi sang v1 |

Ảnh [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png) chụp lúc A và D cùng bật: nhóm `agent-slo` hiện **FIRING (2)**, gồm `AgentHighErrorRate` và `AgentInstanceDown`; `AgentHighLatencyP95` và `LLMCostBurnRateHigh` vẫn INACTIVE. Ảnh chụp ở lần bật lại A + D thứ hai (sau khi Docker Desktop khởi động lại); thời gian trong bảng trên đo ở lần đầu, lúc đó Prometheus API ghi `activeAt` 16:06:52 UTC cho `AgentHighErrorRate` và 16:08:07 UTC cho `AgentInstanceDown`.

**Nhận xét:**

- Thời gian tới lúc firing ≈ thời gian để biểu thức vượt ngưỡng + `for`. Với A: `rate(...[1m])` cần ~30 s dữ liệu lỗi để error rate vượt 5 % (pending sau 35 s), cộng `for: 1m`. Với D: Prometheus scrape mỗi 5 s nên `up == 0` gần như ngay (pending sau 16 s), cộng `for: 30s`.
- Thí nghiệm D dùng `docker pause` chứ không `docker stop`: Prometheus tìm agent bằng DNS service discovery. Container bị stop biến mất khỏi DNS, Prometheus coi như target không còn tồn tại (không phải "down") nên không có alert. Container bị pause vẫn còn trong DNS nhưng không trả lời, nên `up == 0`.
- Khi pause 1 instance, Nginx vẫn route một phần request tới nó cho tới khi hết `proxy_read_timeout`, nên Locust thấy **lỗi 504**. Panel Error rate của agent **không** tính lỗi này: request chưa bao giờ được agent xử lý xong, và metric chỉ phản ánh những gì *chính service đó* nhìn thấy. Vì vậy production cần giám sát thêm ở tầng load balancer (tỉ lệ 5xx của Nginx) hoặc synthetic check từ bên ngoài.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm agent trả lời sai (accuracy từ 100 % xuống ~4 % theo eval ở mục 3), nhưng latency, error rate, số instance đều bình thường, thậm chí còn "đẹp" hơn vì prompt ngắn, rẻ, nhanh. Metric hạ tầng không đo được *đúng hay sai*. Cách phát hiện:

1. **Chặn trước khi deploy:** quality gate trong CI (`run_eval.py --baseline ...` trả exit code 1 khi có regression), như mục 3. Thí nghiệm E chính là tình huống quality gate sẽ chặn: v1 so với baseline v2 có 20 câu regression.
2. **Giám sát chất lượng khi chạy:** chạy golden dataset định kỳ (eval online / synthetic check) vào production và alert khi accuracy giảm. Theo dõi các metric "proxy" của chất lượng như **retrieval hit ratio** (rơi về 0 khi chạy v1) và tokens/câu, alert khi lệch mạnh so với baseline.
3. **Tín hiệu từ người dùng:** feedback 👍/👎, tỉ lệ hỏi lại, và LLM-as-judge chấm mẫu một phần traffic.
4. **Gắn phiên bản vào metric** (`agent_info{version}`) để khi chất lượng giảm, biết ngay phiên bản nào đang chạy và rollback.

### 4.3 Debug bằng trace

**Trace chậm trong ảnh** [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png) (Jaeger → Service `observable-agent` → **Longest First**). Số liệu từng span lấy từ Jaeger API `/api/v3/traces/<trace_id>`:

- `trace_id`: `fe94ae917589927f1e1f48fac4a2e430`
- Tổng thời gian: **1709.0 ms** (`POST /ask`, HTTP 200)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1684.3 ms, 98.6 %)**. Các span còn lại: `agent.run` 1705.1 ms (span cha bao gồm cả LLM), `agent.retrieve_context` chỉ **20.4 ms (1.2 %)**. Attributes của span LLM: `gen_ai.request.model = mock-llm-small`, `gen_ai.usage.input_tokens = 81`, `gen_ai.usage.output_tokens = 46`.
- **Kết luận:** request chậm vì **LLM upstream** chậm, không phải code của agent hay bước retrieval.

**Trace lỗi trong ảnh** [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png) (tag `error=true`): `trace_id` `33f062eb973aac3fc50cde758be93c58`, tổng **112.5 ms**, `POST /ask` trả **503**. Span `chat mock-llm-small` (84.6 ms) và `agent.run` có status ERROR, attribute `error.type = upstream_unavailable` và event **`exception`**: `exception.type = utils.mock_llm.LLMError`, `exception.message = Upstream LLM error: 503 Service Unavailable (mock)`. `agent.retrieve_context` 25.3 ms vẫn thành công; lỗi đến nhanh vì mock trả lỗi sau một nửa latency.

**Log ↔ trace.** Log của hai trace trong ảnh không còn: container agent được tạo lại khi bật thí nghiệm A, nên `docker logs` của container cũ mất theo. Phần liên kết log ↔ trace được kiểm chứng trên hai trace bắt được trước đó cùng stack, cùng tải 30 user. Log được grep bằng `docker logs` theo `trace_id`; trace được mở lại trong Jaeger bằng chính `trace_id` đó.

Trace chậm `bb8c23472eb2df1d744d704a7efe8da5`: tổng **1726.9 ms**, span `chat mock-llm-small` **1652.8 ms (95.7 %)**, `agent.retrieve_context` 23.4 ms. Dòng log tương ứng (`llm_ms` 1644.0 khớp với span LLM, `ms` 1722.9 khớp với tổng thời gian request):

```json
{"ts": "2026-11-25T12:59:49.589285+00:00", "level": "INFO", "service": "observable-agent", "instance": "ce1153e2706f", "event": "llm_call", "trace_id": "bb8c23472eb2df1d744d704a7efe8da5", "span_id": "bff28a47ac4c3e89", "request_id": "7e96a01ce6f2", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 73, "output_tokens": 35, "cost_usd": 3.2e-05, "llm_ms": 1644.0}
{"ts": "2026-11-25T12:59:49.597583+00:00", "level": "INFO", "service": "observable-agent", "instance": "ce1153e2706f", "event": "http_request", "trace_id": "bb8c23472eb2df1d744d704a7efe8da5", "span_id": "bff28a47ac4c3e89", "request_id": "7e96a01ce6f2", "method": "POST", "route": "/ask", "status": 200, "ms": 1722.9}
```

Trace lỗi `334156c09e407311335058659a893a99`: tổng 121.5 ms, `POST /ask` trả 503, span `chat mock-llm-small` 92.1 ms với event `exception` (`LLMError`). Log tương ứng (`llm_error` → `http_request` status 503):

```json
{"ts": "2026-11-25T13:00:00.627141+00:00", "level": "INFO", "service": "observable-agent", "instance": "3ec826229380", "event": "llm_error", "trace_id": "334156c09e407311335058659a893a99", "span_id": "765dfbac26a92e47", "request_id": "f87351affe9d", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T13:00:00.627620+00:00", "level": "INFO", "service": "observable-agent", "instance": "3ec826229380", "event": "http_request", "trace_id": "334156c09e407311335058659a893a99", "span_id": "765dfbac26a92e47", "request_id": "f87351affe9d", "method": "POST", "route": "/ask", "status": 503, "ms": 120.6}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh: `docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`, sau `up -d --scale agent=<n>`. Số liệu là dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.35 | 200 | 440 | 1700 | 67 / 7047 (0.95 %) |
| 3 | 60.10 | 190 | 240 | 1700 | 64 / 7195 (0.89 %) |

> Dùng `--no-deps` vì `docker compose run locust` mặc định khởi động lại các service phụ thuộc theo `replicas: 2` trong `docker-compose.yml`. Lần chạy đầu không có `--no-deps` bị thêm `agent-2` giữa chừng nên đã bỏ và chạy lại.

**Giải thích kết quả:**

- **RPS gần như không đổi (59.35 so với 60.10)** vì đây là tải **closed-loop**: 100 user, mỗi user nghỉ 0.5–2 s (`between(0.5, 2)`) giữa các lần gọi. Throughput tối đa ≈ 100 / (1.25 s nghỉ trung bình + ~0.23 s latency) ≈ 67 req/s tổng (CSV aggregated: 65.4 và 66.2 req/s). Scale thêm instance không làm người dùng gửi nhiều hơn, nên RPS do phía client quyết định.
- **p95 cải thiện rõ: 440 ms → 240 ms.** Một container bị giới hạn **0.5 CPU** phải gánh ~60 req/s, sát mức bão hoà, nên request bắt đầu xếp hàng và p95 tăng. Ba instance chia ra ~20 req/s mỗi cái, hết xếp hàng, p95 về mức "nền" (~240 ms ≈ latency của chính LLM mock).
- **p99 vẫn 1700 ms ở cả hai lần**, failures vẫn ~0.9 %. Đây là tail latency và lỗi của **LLM upstream** (3 % request chậm ~1.5 s, ~1 % lỗi 503). Scale agent không sửa được vì nút thắt không nằm ở agent. Panel "LLM latency p95 theo model" giữ ~197–199 ms bất kể số instance.
- **Kết luận:** scale ngang chỉ giúp khi nút thắt là CPU của agent (ở đây: p95 khi 1 instance bị bão hoà). Khi đã đủ instance, latency bị chặn dưới bởi LLM. Muốn giảm tiếp p95/p99 phải xử lý phía LLM: timeout + retry/hedging cho request chậm, cache câu trả lời, model nhanh hơn, hoặc streaming.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Percentile quan trọng hơn average.** Chỉ 3 % request chậm làm p99 tăng 6.5 lần trong khi avg tăng 21 %. SLO và alert phải đặt theo p95/p99, và phải đọc chúng cùng throughput (Little's law) để biết hệ thống đang xếp hàng hay chỉ có tail của upstream.
  2. **Chất lượng phải đo riêng bằng eval.** Agent v1 có accuracy ~4 % (v2 sau khi sửa: 100 %), nhưng nhanh và rẻ hơn, nên không metric hạ tầng nào (latency, error rate, instances) chuyển đỏ khi đổi sang v1. Golden dataset + quality gate (exit code) trong CI là cách duy nhất chặn được regression chất lượng trước khi deploy; so phiên bản bằng *cost / câu đúng*.
  3. **Metrics, traces, logs bổ sung cho nhau.** Dashboard báo p99 cao → Jaeger chỉ ra span `chat mock-llm-small` chiếm 95.7 % thời gian → `trace_id` dẫn tới đúng dòng log JSON. Mỗi trụ cột chỉ nhìn thấy những gì *chính service đó* thấy: lỗi 504 ở Nginx không hiện trên metric của agent, nên cần thêm giám sát ở tầng load balancer.
- Nếu đưa agent này lên production thật, sẽ thêm metric / alert:
  - **Synthetic check từ bên ngoài** (gọi `/ask` qua load balancer mỗi phút) và metric/alert ở tầng Nginx (tỉ lệ 5xx/504 của upstream), để bắt lỗi mà agent không tự thấy.
  - **Eval online / giám sát chất lượng:** chạy golden dataset định kỳ trên production và alert khi accuracy giảm; theo dõi retrieval hit ratio (alert khi giảm mạnh, như thí nghiệm E) và feedback 👍/👎 của người dùng.
  - **Alert theo error budget burn rate** (multi-window, vd 1 h và 6 h) thay vì ngưỡng tức thời, để giảm alert fatigue.
  - **Budget theo ngày/tháng** cho cost (không chỉ burn rate theo giờ) và cost theo model/phiên bản; alert về saturation (`agent_in_flight_requests`, CPU/memory container) trước khi p95 vượt SLO.

---

### Ghi chú về môi trường chạy

- Trên Windows, `run_eval.py` đọc/ghi JSON bằng encoding mặc định (cp1252) nên lỗi với tiếng Việt. Eval được chạy ở **UTF-8 mode** (`PYTHONUTF8=1`), không sửa script.
- `production/Dockerfile` của đề tạo user `agent` có home `/app` (`useradd -d /app`) nhưng copy package vào `/home/agent/.local`, nên Python không tìm thấy `uvicorn` và container restart liên tục. Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local` để Python tìm đúng chỗ.
- Image `grafana/grafana:13.2.4` trên máy bị hỏng (binary bị cắt cụt sau một lần pull bị gián đoạn vì hết RAM), nên chỉ trên máy này đã chạy Grafana bằng `grafana/grafana:13.2.4-ubuntu` (cùng phiên bản 13.2.4) qua một file override cục bộ, không đổi `docker-compose.yml` trong repo.
