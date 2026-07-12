# Weaver API — Phân Tích Hệ Thống

## 1. Tổng Quan

**Weaver** là microservice enrichment thuộc hệ thống **Binh Chủng Trinh Sát (BCTS)**, chịu trách nhiệm làm giàu dữ liệu lead (khách hàng tiềm năng) bằng cách:

- Trích xuất tên công ty và domain từ nội dung bài đăng
- Crawl website công ty để lấy mô tả
- Cache kết quả bằng Redis để tối ưu tốc độ

---

## 2. Kiến Trúc Hệ Thống

### 2.1. Sơ Đồ Components

```
┌──────────────┐     HTTP POST /weaver     ┌──────────────────┐
│   n8n        │ ──────────────────────────►│   weaver-api     │
│ (Workflow)   │◄───────────────────────────│  :8001           │
└──────────────┘     JSON (lead + enriched)  └──────┬───────────┘
                                                    │
                          ┌─────────────────────────┼──────────┐
                          │                         │          │
                          ▼                         ▼          ▼
                   ┌──────────────┐        ┌──────────────┐
                   │   Redis      │        │  Crawl4AI /  │
                   │  :6379       │        │  httpx       │
                   │  (cache)     │        │  (crawler)   │
                   └──────────────┘        └──────────────┘
```

### 2.2. Docker Services

| Service | Image | Port | Network |
|---------|-------|------|---------|
| `n8n` | `n8nio/n8n:latest` | 5678 | bcts-network |
| `redis` | `redis:7` | 6379 | bcts-network |
| `weaver-api` | build từ `./weaver-api` | 8001 | bcts-network |
| `9router` | `decolua/9router` | 20128 | bcts-network |

---

## 3. API Endpoints

### 3.1. `POST /weaver` — Enrich Lead

**Input:**
```json
{
  "lead_id": "uuid",
  "total_score": 85,
  "lead": {
    "lead_id": "...",
    "source": "discord | reddit | hackernews | ...",
    "content": "We are hiring at TechCorp! Check out https://techcorp.com",
    "title": "...",
    "author": "...",
    "url": "https://techcorp.com",
    "created_at": "2026-06-23T12:00:00Z",
    "keywords_matched": ["hiring"],
    "type": "post | message",
    "raw_data": {}
  }
}
```

**Output:**
```json
{
  "lead_id": "uuid",
  "total_score": 85,
  "lead": { /* ⚠️ Giữ nguyên toàn bộ lead gốc */ },
  "enriched_data": {
    "company_domain": "techcorp.com",
    "company_description": "Nationwide field techs...",
    "company_name": "TechCorp",
    "weaver_available": true,
    "error": null
  }
}
```

### 3.2. `GET /health` — Health Check

```json
{ "status": "ok", "service": "weaver-api", "version": "3.1.0" }
```

---

## 4. Pipeline Xử Lý Enrichment

```
┌──────────────┐
│  Nhận Input  │──► deepcopy() — giữ nguyên payload gốc
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Step 1: URL  │──► Nếu lead.url có → parse domain
│ Extraction   │──► Nếu null → extract_domain_from_text(content)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Step 2:      │──► Regex patterns cho hiring/startup/forum/lead
│ Company Name │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Step 3:      │──► Kiểm tra Redis cache (key: weaver:v3:crawl:{domain})
│ Crawl        │──► Nếu miss → Crawl4AI (ưu tiên) hoặc httpx (fallback)
│ Homepage     │──► Extract title, meta/og description, JSON-LD
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Step 4:      │──► Nếu crawl thành công → dùng mô tả từ website
│ Description  │──► Nếu crawl thất bại → generate từ content gốc
│ Generation   │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Step 5:      │──► Append enriched_data vào payload gốc
│ Return       │──► Trả về JSON đầy đủ
└──────────────┘
```

---

## 5. Components Chi Tiết

### 5.1. `main.py` — FastAPI Application

- **CORS**: Mở `allow_origins=["*"]` cho phép mọi origin
- **POST /weaver**: Dùng `deepcopy()` để giữ nguyên payload gốc, sau đó append `enriched_data`
- **Version**: `3.1.0`
- **Log format**: JSON-structured logging (`{"time":"...","name":"...","level":"...","message":"..."}`)

### 5.2. `services.py` — Core Logic

#### Optional Dependencies (Graceful Degradation)
| Package | Flag | Vai trò |
|---------|------|---------|
| `beautifulsoup4` | `HAS_SOUP` | Parse HTML để trích xuất meta tags |
| `redis.asyncio` | `HAS_REDIS` | Cache kết quả crawl (24h TTL) |
| `crawl4ai` | `HAS_CRAWL4AI` | Crawl website chuyên nghiệp (Playwright-based) |

Nếu thiếu package → tự động fallback:
- Không Redis → chạy không cache
- Không Crawl4AI → dùng `httpx` + `BeautifulSoup`

#### Regex Patterns — Trích Xuất Tên Công Ty

| Pattern Type | Ví dụ Match |
|-------------|-------------|
| **Hiring** | `"We're hiring at TechCorp"`, `"Acme is hiring"` |
| **Startup** | `"I founded a startup called TechCorp"` |
| **Forum** | `"I work at Acme"`, `"CTO at TechCorp"` |
| **Lead** | `"Hi team, my name is John from Acme"` |

#### Domain Skip List
Các domain social media bị bỏ qua:
`discord.com`, `reddit.com`, `twitter.com`, `x.com`, `linkedin.com`,
`facebook.com`, `instagram.com`, `youtube.com`, `tiktok.com`,
`medium.com`, `github.com`, `gitlab.com`, v.v.

### 5.3. `schemas.py` — Pydantic Models

| Model | Mục đích |
|-------|---------|
| `LeadObject` | Lead gốc với đầy đủ fields |
| `EnrichedData` | Kết quả enrichment (domain, description, name, available, error) |
| `WeaverEnvelope` | Envelope chứa cả lead + enriched_data |

---

## 6. Redis Cache

- **Connection**: `redis://redis:6379/0`
- **Key format**: `weaver:v3:crawl:{domain}`
- **TTL**: 86400s (24 giờ)
- **Cache hit/miss**: Log ở level INFO

**Luồng cache:**
```
Request → Kiểm tra Redis → Hit? → Return cached
                        → Miss? → Crawl website → Lưu vào Redis → Return
```

---

## 7. Crawl Strategy

### 7.1. Crawl4AI (Preferred)
- Retry tối đa **2 lần**
- Timeout: **15 giây**
- Backoff: `1s * attempt_number`
- Trích xuất: title, meta description, OG description, JSON-LD

### 7.2. httpx + BeautifulSoup (Fallback)
- Timeout: **10 giây**
- User-Agent: `Mozilla/5.0`
- Follow redirects: `true`

### 7.3. Content Fallback
Khi crawl thất bại hoàn toàn, Weaver tự động sinh mô tả từ content gốc của lead:
- Nếu có `company_name`: `"{name} is a company mentioned in a lead post. {first_sentence}"`
- Nếu không có: lấy 2-3 câu có nghĩa từ content

---

## 8. Xử Lý Lỗi

| Tình huống | Hành vi |
|-----------|---------|
| Không có URL & không extract được domain | `error: "Missing URL"` |
| URL là social media | Bỏ qua, trả về null |
| Crawl timeout (15s) | Thử lại ×2, nếu fail → content fallback |
| Crawl 403/429/SSL/DNS error | Thử lại ×2, nếu fail → content fallback |
| Redis unavailable | Chạy không cache, log warning |
| Không có crawl4ai | Tự động fallback httpx |
| Payload JSON không hợp lệ | `body = {}` |

---

## 9. Logging

Format JSON-structured:
```json
{"time":"2026-06-23T12:00:00","name":"bcts.weaver","level":"INFO","message":"POST /weaver lead_id=uuid-123 source=hackernews url=https://techcorp.com"}
```

Các sự kiện được log:
- Request đến (`lead_id`, `source`, `url`)
- Cache hit/miss
- Crawl success/failure (kèm attempt number)
- Redis unavailable
- Kết quả trả về

---

## 10. Biến Môi Trường

| Variable | Default | Mô tả |
|----------|---------|-------|
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `REDIS_CACHE_TTL` | `86400` | Cache TTL (giây) |

---

## 11. Nguyên Tắc Thiết Kế

1. **Enrichment-only**: Weaver chỉ **thêm** dữ liệu, không bao giờ xoá/sửa lead gốc
2. **Graceful degradation**: Thiếu dependency vẫn chạy được với fallback
3. **Resilience**: Retry + timeout + backoff cho crawl
4. **Null safety**: Mọi lỗi đều trả về null fields, không hallucinate
5. **Cache first**: Redis cache giảm tải crawl trùng lặp
