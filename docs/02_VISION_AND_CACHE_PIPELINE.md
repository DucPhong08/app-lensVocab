# 02. Chi Tiết Luồng AI Vision & Multi-Tier Cache

Tài liệu này mô tả chi tiết pipeline xử lý ảnh từ lúc client gửi request lên endpoint `POST /api/v1/scan` cho đến khi trả về flashcard hoàn chỉnh.

---

## 1. Sơ Đồ Tuần Tự (Sequence Flow)

```
Client              FastAPI [/scan]             Redis               AWS Rekognition      MongoDB Atlas        AWS Bedrock/Polly
  │                        │                      │                         │                   │                     │
  │── 1. Upload ảnh ──────►│                      │                         │                   │                     │
  │   (multipart/form)     │── 2. Check Quota ───►│                         │                   │                     │
  │                        │   (SET NX + DECR)    │                         │                   │                     │
  │                        │◄── OK (Còn lượt) ────│                         │                   │                     │
  │                        │                                                │                   │                     │
  │                        │── 3. Validate Format/Size (Max 5MB)            │                   │                     │
  │                        │                                                │                   │                     │
  │                        │── 4. detect_labels(Bytes) ────────────────────►│                   │                     │
  │                        │◄── Labels, BoundingBox, Confidence ────────────│                   │                     │
  │                        │                                                │                   │                     │
  │                        │── 5. Confidence < 50%? ─────────────────────────────────────────────────────────────────►│
  │                        │◄── Bedrock Scene Fallback Keyword ──────────────────────────────────────────────────────│
  │                        │                                                │                   │                     │
  │                        │── 6. Tra cứu Tier 1: Redis Key ───────────────►│                   │                     │
  │                        │    [Cache Hit? -> Trả về luôn]                 │                   │                     │
  │                        │                                                                    │                     │
  │                        │── 7. Tra cứu Tier 2a: Mongo Exact Match ──────────────────────────►│                     │
  │                        │    [Hit? -> Lưu Redis & Trả về]                                    │                     │
  │                        │                                                                    │                     │
  │                        │── 8. Tra cứu Tier 2b: Titan Embedding + $vectorSearch ─────────────►│                     │
  │                        │    [Score >= 0.85? -> Lưu Redis & Trả về]                          │                     │
  │                        │                                                                    │                     │
  │                        │── 9. Cache Miss: Gọi Bedrock Nova Lite sinh Flashcard ──────────────────────────────────►│
  │                        │   + Gọi AWS Polly sinh MP3 Audio ───────────────────────────────────────────────────────►│
  │                        │◄── Trả về Flashcard đầy đủ ─────────────────────────────────────────────────────────────│
  │◄── 10. Trả về Client ──│
```

---

## 2. Chi Tiết Từng Bước Trong Pipeline

### Bước 1 & 2: Bảo Vệ Cổng Vào & Khấu Trừ Quota
1. **Kiểm tra trạng thái Bảo trì (`maintenance_mode`):**
   - Đọc cài đặt từ `SystemSetting`. Nếu đang bảo trì, lập tức trả về `HTTP 503 SERVICE UNAVAILABLE`.
2. **Khấu trừ Quota bằng Redis Atomic:**
   - Dùng thuật toán `SET NX` kết hợp lệnh `DECR` nguyên tử trong Redis.
   - Nếu `new_quota < 0`: Tự động phục hồi bằng `INCR` và ném lỗi `HTTP 403 FORBIDDEN` (`QUOTA_EXCEEDED`).

### Bước 3: Xác Thực Dữ Liệu Đầu Vào (Input Validation)
* **Kích thước file:** Giới hạn tối đa **5MB** (`MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024`). Đây là ngưỡng tối đa của AWS Rekognition khi truyền bytes trực tiếp. Nếu vượt quá, trả về `HTTP 413 REQUEST ENTITY TOO LARGE`.
* **Định dạng file:** Chỉ chấp nhận `image/jpeg` và `image/png`. Nếu gửi định dạng khác, trả về `HTTP 400 BAD REQUEST` (`INVALID_IMAGE_FORMAT`).

### Bước 4: Nhận Diện Thực Thể (AWS Rekognition)
* Gọi API `detect_labels` qua luồng phụ non-blocking (`asyncio.to_thread`).
* **Thuật toán Smart Selection:**
  * Lọc qua danh sách các nhãn trả về.
  * Ưu tiên các nhãn có tọa độ cụ thể (`Instances` với `BoundingBox`) để gán cho đồ vật người dùng đang hướng ống kính vào.
  * Bóc tách các siêu dữ liệu hỗ trợ: `Categories`, `Aliases`, `Parents`.

### Bước 5: Cơ Chế Cứu Nguy (Graceful Degradation)
* Nếu nhãn nhận diện có độ tin cậy thấp hơn ngưỡng quy định (`top_confidence < 50.0%`):
  * Không vội ném lỗi không nhận diện được.
  * Gom toàn bộ mô tả bối cảnh xung quanh gửi tới **AWS Bedrock Nova Lite** với system prompt chuyên biệt.
  * Bedrock sẽ phân tích bối cảnh để suy luận ra từ vựng tiếng Anh trình độ A1-A2 phù hợp nhất với khung cảnh (Ví dụ: Nhận diện thấy "Wood" nhưng bối cảnh là phòng học $\rightarrow$ Bedrock suy luận ra "Desk").

### Bước 6 $\rightarrow$ Bước 9: Giải Thuật Multi-Tier Cache Resolver
1. **Tier 1 - Redis Cache ($0, < 5ms):**
   - Kiểm tra key `flashcard:{keyword}`. Nếu trúng, trả về ngay.
2. **Tier 2a - MongoDB Exact Match ($0, < 15ms):**
   - Tìm theo index duy nhất `keyword` trong `global_flashcards`. Nếu trúng, ghi ngược lại vào Redis rồi trả về.
3. **Tier 2b - MongoDB Atlas Vector Search:**
   - Gọi Bedrock Titan Text v2 sinh vector 1024 chiều từ keyword.
   - Thực thi pipeline `$vectorSearch` với độ tương đồng `cosine >= 0.85`.
   - **Lưu ý kỹ thuật:** Pipeline phải có stage `"$addFields": {"id": "$_id"}` để ánh xạ ID chính xác cho Beanie ODM.
4. **Tier 3 - AWS Bedrock Nova Lite + AWS Polly (Cache Miss):**
   - Kích hoạt khi toàn bộ 3 tầng cache trên đều không có kết quả.
   - Bedrock sinh phiên âm IPA, nghĩa tiếng Việt, 2 câu ví dụ ngắn chuẩn A1-A2, các từ liên quan (dạng JSON hợp lệ).
   - AWS Polly chuyển từ vựng thành audio MP3 (chuỗi base64).

---

## 3. Quản Lý Ngoại Lệ & An Toàn Hạ Tầng

* **Bắt lỗi `botocore.exceptions.ClientError`:**
  * Lỗi do ảnh client gửi (`InvalidImageFormatException`): Trả về `HTTP 400 BAD REQUEST`.
  * Lỗi do dịch vụ AWS (hết quota, throttling, timeout): Trả về `HTTP 502 BAD GATEWAY` (`AWS_VISION_UNAVAILABLE`).
* **Tuyệt đối không để rò rỉ:** Không bao giờ để bung exception 500 kèm stack trace chứa IAM role hay endpoint AWS ra client.

---

## 4. Giao Thức Streaming Thời Gian Thực (Server-Sent Events - SSE)

Bên cạnh endpoint truyền thống `POST /vision/scan`, hệ thống cung cấp endpoint streaming thời gian thực `POST /vision/scan/stream` với `Content-Type: text/event-stream`:

* **Mục đích:** Tối ưu trải nghiệm người dùng khi gặp Cache Miss (chuỗi AI Rekognition + Bedrock + Polly mất 2.5s–4s). Frontend nhận diện nhãn và vẽ khung Bounding Box ngay ở giây đầu tiên, sau đó nhận dần nội dung Flashcard và file âm thanh phát âm.
* **Header tối ưu Nginx:** Đính kèm `X-Accel-Buffering: no` để tắt buffer của Reverse Proxy, đảm bảo client nhận gói tin ngay lập tức.
* **Chuỗi sự kiện (Event Sequence):**
  1. `event: status` — Báo trạng thái từng chặng (`START`, `LOOKUP_CACHE`, `AI_GENERATING`, `SYNTHESIZING_AUDIO`).
  2. `event: vision_detected` — Trả về nhãn nhận diện, độ tin cậy và tọa độ Bounding Box từ AWS Rekognition để Frontend vẽ khung viền ngay lập tức.
  3. `event: vocab_content` — Trả về phiên âm IPA, nghĩa tiếng Việt, câu ví dụ và từ liên quan (từ Cache hoặc Bedrock Nova Lite).
  4. `event: audio_ready` — Trả về dữ liệu âm thanh phát âm MP3 (chuỗi base64) từ AWS Polly.
  5. `event: done` — Xác nhận hoàn thành toàn bộ chu trình xử lý.
