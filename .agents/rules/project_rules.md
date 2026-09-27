---
trigger: always_on
description: LensVocab Backend Core Engineering Rules, Style, and Architecture Constraints
---

# LensVocab Backend - Project Operating Rules & Conventions

Tài liệu này định hình phong cách lập trình, kiến trúc và các quy tắc bất di bất dịch của dự án **LensVocab Backend**. Bất kỳ AI Agent hoặc kỹ sư nào tham gia phát triển dự án này đều **BẮT BUỘC** phải tuân thủ nghiêm ngặt.

---

## 1. Phong cách Code & Naming Conventions

- **Ngôn ngữ:** Python 3.12 (sử dụng typing hiện đại: `str | None`, `list[str]`, `from __future__ import annotations`).
- **Naming Convention:**
  - **100% `snake_case`** cho biến, hàm, tham số, trường dữ liệu trong Database và **toàn bộ JSON contract (Request / Response DTO)** gửi cho Frontend.
  - **TUYỆT ĐỐI KHÔNG** dùng `camelCase` hoặc thêm `alias_generator=to_camel` để tránh overhead và lỗi lệch chuẩn giữa các router.
  - Tên class: `PascalCase`. Hằng số: `UPPER_SNAKE_CASE`.
- **Triết lý viết code:** Viết code tường minh, tối giản theo chuẩn PEP 8. Không thêm chú thích hiển nhiên, không over-engineering.

---

## 2. Kiến trúc & Thiết kế (KISS & YAGNI)

- **Active Record qua Beanie ODM:**
  - `User`, `GlobalFlashcard`, `UserFlashcard`, `ReviewLog`, `SystemSetting` kế thừa từ `beanie.Document`.
  - Gọi trực tiếp `Model.find()`, `model.save()`, `model.insert()`.
  - **CẤM TỰ Ý TẠO LẠI THƯ MỤC `app/repositories/`:** Beanie đã là một ORM/ODM chuẩn Active Record. Việc bọc thêm một lớp Repository chỉ là pass-through rỗng, vi phạm nguyên tắc YAGNI.
- **3 Tầng Cấu hình (3-Tier Configuration):**
  1. `.env`: Chỉ chứa hạ tầng vật lý và bí mật (Mongo URL, Redis URL, Secret Key, AWS Credentials).
  2. `SystemSetting`: Cấu hình động trong MongoDB cho Quản trị viên (Hot-reload, không cần restart server).
  3. `UserPreferences`: Tùy chọn học tập cá nhân của người dùng lưu trong `User.preferences`.

---

## 3. Quy chuẩn Tích hợp AWS & AI Services

- **Boto3 Non-blocking:** Toàn bộ lệnh gọi AWS SDK (`boto3`) đều là synchronous blocking I/O, do đó **bắt buộc** phải bọc qua `asyncio.to_thread(...)` khi gọi trong các async function/handler.
- **Adaptive Retries & Timeout:** Client Boto3 phải luôn gắn `_BOTO_CONFIG` với `adaptive retries` và timeout rõ ràng.
- **Bắt lỗi AWS cụ thể:**
  - Khi gọi Rekognition / Bedrock / Polly, phải bắt `botocore.exceptions.ClientError`.
  - Phân tách rạch ròi: lỗi dữ liệu client gửi sai (trả về HTTP 400) vs lỗi hạ tầng AWS throttle/unavailable (trả về HTTP 502/503). Tuyệt đối không để bung lỗi 500 kèm stack trace.
- **Khóa cứng Vector Dimension:** Model `amazon.titan-embed-text-v2:0` tạo vector **1024 dimensions**, khớp cứng với MongoDB Atlas Vector Search Index (`vocab_embedding_index`). Không được tự ý đổi model embedding sang loại có số chiều khác (như 1536) nếu không migrate lại database index.

---

## 4. Quota, Bảo mật & Phân quyền Gói cước

- **Bảo vệ toàn diện API:** Mọi endpoint nghiệp vụ liên quan đến tài nguyên cá nhân (`/scan`, `/flashcards`, `/review`, `/users/me/preferences`) bắt buộc phải bọc qua dependency `get_current_user`.
- **Kiểm soát Quota bằng Redis:**
  - Khấu trừ Quota phải thực hiện **Atomic** trên Redis bằng lệnh `SET NX` và `DECR`, tuyệt đối không dùng giải thuật "GET rồi mới check" gây race condition khi nhiều request đồng thời tới.
  - Phân tầng gói cước: `AccountTier.FREE` (10 scan/ngày, giọng đọc cơ bản) vs `AccountTier.PREMIUM` (200 scan/ngày, mở khóa toàn bộ giọng đọc Polly và tốc độ phát âm).

---

## 5. Quy tắc Kiểm thử (Testing) & Khởi tạo Model Offline

- **Khởi tạo Document trong Test:**
  - Khi mock unit test mà không có kết nối MongoDB thật, **CẤM** gọi `User(...)` hay `SystemSetting(...)` trực tiếp (Beanie sẽ gọi `get_motor_collection()` và ném lỗi `CollectionWasNotInitialized`).
  - **Bắt buộc** dùng `User.model_construct(...)` hoặc `SystemSetting.model_construct(...)` của Pydantic v2 để khởi tạo offline.
- **Mock External Calls:** Toàn bộ lệnh gọi ra ngoài (AWS Rekognition, Bedrock, Polly, Redis) trong unit tests phải được mock bằng `unittest.mock.patch` hoặc `AsyncMock`.
- **Lệnh chạy test:** Luôn chạy bằng môi trường ảo của dự án:
  ```bash
  .venv/bin/python -m unittest discover tests
  ```

---

## 6. Tài liệu (Documentation)

- **Cập nhật Tài liệu Bắt buộc:** Khi thêm endpoint mới, thay đổi model, hoặc sửa luồng xử lý AI/Cache, Agent/Developer **bắt buộc phải cập nhật tài liệu tương ứng trong thư mục `docs/`** trước khi kết thúc task.
