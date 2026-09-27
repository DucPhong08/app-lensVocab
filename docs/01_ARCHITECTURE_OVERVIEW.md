# 01. Tổng Quan Kiến Trúc & Thiết Kế Dữ Liệu

Tài liệu này mô tả kiến trúc tổng thể, mô hình cấu hình 3 tầng, thiết kế dữ liệu và các quyết định kiến trúc quan trọng của hệ thống **LensVocab Backend**.

---

## 1. Sơ Đồ Kiến Trúc Tổng Thể

```
                    ┌─────────────────────────┐
                    │     Frontend Client     │
                    │   (Mobile App / Web)    │
                    └────────────┬────────────┘
                                 │ HTTP Bearer JWT
                                 ▼
                    ┌─────────────────────────┐
                    │       FastAPI API       │
                    │  (Python 3.12 / Async)  │
                    └──────┬──────────┬───────┘
                           │          │
         ┌─────────────────┘          └────────────────┐
         ▼                                             ▼
┌──────────────────┐                         ┌──────────────────┐
│   Redis Cache    │                         │  MongoDB Atlas   │
│ - Exact Match    │                         │ - Users          │
│ - Daily Quota    │                         │ - Global Cards   │
│ - Auth Blacklist │                         │ - User Cards     │
└──────────────────┘                         │ - Review Logs    │
                                             │ - System Settings│
                                             │ - $vectorSearch  │
                                             └──────────────────┘
                                                       │
                                                       ▼
                                             ┌──────────────────┐
                                             │  Pure AWS Cloud  │
                                             │ - Rekognition    │
                                             │ - Bedrock Nova   │
                                             │ - Titan Vector   │
                                             │ - Polly Audio    │
                                             └──────────────────┘
```

---

## 2. Mô Hình 3 Tầng Cấu Hình (3-Tier Configuration)

Để cân bằng giữa tính ổn định của hạ tầng và sự linh hoạt trong kinh doanh, dự án chia cấu hình thành 3 tầng:

### Tầng 1: Biến môi trường `.env` (Hạ tầng vật lý & Bí mật)
* **Đặc điểm:** Yêu cầu khởi động lại ứng dụng khi thay đổi.
* **Bao gồm:**
  * `MONGODB_URL`, `MONGODB_DB_NAME`: Chuỗi kết nối kho dữ liệu.
  * `REDIS_URL`: Chuỗi kết nối cache.
  * `SECRET_KEY`, `ALGORITHM`: Chìa khóa mã hóa JWT token.
  * `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`: Cặp khóa IAM cấp quyền gọi dịch vụ AWS.
  * `BEDROCK_EMBEDDING_MODEL_ID`: Cố định `amazon.titan-embed-text-v2:0` vì gắn liền với 1024 chiều vector của index MongoDB Atlas.

### Tầng 2: Cài đặt hệ thống động `SystemSetting` (Quản trị viên)
* **Đặc điểm:** Lưu trong collection `system_settings` (Singleton `id: "global_config"`). Admin thay đổi qua API `PATCH /api/v1/admin/settings` và có **hiệu lực ngay lập tức (Hot-reload)** mà không cần deploy lại.
* **Bao gồm:**
  * `free_daily_quota`: Hạn mức scan ngày của gói Free (mặc định 10).
  * `premium_daily_quota`: Hạn mức scan ngày của gói Premium (mặc định 200 - chốt chặn chống bot).
  * `free_daily_review_cap`: Giới hạn từ ôn tập hàng ngày gói Free (mặc định 15).
  * `premium_daily_review_cap`: Giới hạn từ ôn tập hàng ngày gói Premium (mặc định 9999).
  * `vision_confidence_threshold`: Ngưỡng Rekognition tin cậy (mặc định 0.50).
  * `semantic_similarity_threshold`: Ngưỡng vector search cache hit (mặc định 0.85).
  * `maintenance_mode`: Công tắc khẩn cấp đưa toàn bộ luồng scan vào chế độ bảo trì (HTTP 503).

### Tầng 3: Tùy chọn người dùng `UserPreferences` (Học viên)
* **Đặc điểm:** Nhúng trực tiếp trong document `User` (trường `preferences`). Học viên tự điều chỉnh qua `PATCH /api/v1/users/me/preferences`.
* **Bao gồm:**
  * `preferred_voice_id`: Giọng đọc phát âm AWS Polly (`"Joanna"`, `"Matthew"`, `"Amy"`...).
  * `voice_speed`: Tốc độ đọc (`0.75x`, `1.0x`, `1.25x`).
  * `daily_review_goal`: Mục tiêu số từ ôn tập mỗi ngày của cá nhân.
  * `target_language`: Ngôn ngữ dịch nghĩa (mặc định `"vi"`).

---

## 3. Chính Sách Gói Cước (Tier Policies Matrix)

Được quản lý tập trung tại `app/services/tier_service.py`:

| Tiêu chí | Gói FREE | Gói PREMIUM |
| :--- | :--- | :--- |
| **Hạn mức Scan ảnh/ngày** | 10 lượt (Admin có thể tăng giảm) | 200 lượt (an toàn chống bot) |
| **Trần ôn tập hàng ngày** | Tối đa 20 từ/ngày (chống nản) | Không giới hạn (hoặc tự chọn tới 100 từ) |
| **Giọng đọc AWS Polly** | Cố định `Joanna` (US Nữ) | Tự do chọn: `Joanna`, `Matthew`, `Amy`, `Brian`, `Olivia` |
| **Công nghệ đọc Polly** | Standard Voice | Neural Engine (chuẩn âm thanh tự nhiên) |
| **Tốc độ đọc** | Cố định `1.0x` | Tùy chỉnh `0.75x`, `1.0x`, `1.25x` |

---

## 4. Thiết Kế Dữ Liệu (Beanie Document Models)

Dự án áp dụng mô hình **Active Record** thông qua Beanie ODM (`app/models/models.py`):

### 1. `User` (Collection: `users`)
* Quản lý tài khoản, mật khẩu băm bcrypt, trạng thái kích hoạt.
* Theo dõi hạn mức scan trong ngày: `daily_quota_left`, `quota_reset_date`.
* Chứa trường nhúng `preferences: UserPreferences`.

### 2. `GlobalFlashcard` (Collection: `global_flashcards`)
* Kho từ vựng dùng chung toàn hệ thống.
* `keyword`: Từ vựng tiếng Anh (Unique Index).
* `pronunciation`, `meaning_vi`, `example_1`, `example_2`, `related_words`.
* `audio_base64`: Chuỗi âm thanh MP3 từ AWS Polly.
* `embedding`: Mảng 1024 số thực (Vector 1024 dims từ Titan Embeddings v2).

### 3. `UserFlashcard` (Collection: `user_flashcards`)
* Bản ghi liên kết giữa `user_id` và `global_flashcard_id`.
* **Unique Composite Index:** `[("user_id", ASCENDING), ("global_flashcard_id", ASCENDING)]` ngăn chặn hoàn toàn việc tạo trùng thẻ khi click đúp.
* Các chỉ số Spaced Repetition: `interval`, `repetitions`, `efactor`, `next_review_date`, `total_reviews`.

### 4. `ReviewLog` (Collection: `review_logs`)
* Lưu nhật ký lịch sử từng lượt học viên chấm điểm ôn tập để theo dõi tiến trình tiến bộ.
* Lưu snapshot trước và sau khi tính thuật toán: `interval_before/after`, `efactor_before/after`, `quality` (0-5).

### 5. `SystemSetting` (Collection: `system_settings`)
* Singleton document với `id: "global_config"` lưu trữ toàn bộ các thông số vận hành động của hệ thống.
