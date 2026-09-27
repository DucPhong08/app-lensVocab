# 04. Cẩm Nang Phát Triển & Mở Rộng Dự Án (Developer & Agent Guide)

Tài liệu này dành cho các kỹ sư backend và AI Agent tiếp quản dự án. Hướng dẫn chi tiết cách mở rộng tính năng, bảo trì mã nguồn và quy chuẩn bắt buộc cần tuân thủ.

---

## 1. Quy Trình Mở Rộng Hệ Thống (Separation of Concerns)

### 1. Kiến Trúc 4 Tầng Chuẩn (4-Layer Pattern)
1. **`app/models/` (Database Entities):** Chứa Beanie Documents tách biệt theo domain (`user.py`, `flashcard.py`, `review.py`, `setting.py`). Không để lẫn Pydantic DTO hay API schemas vào đây.
2. **`app/schemas/` (API Contracts / DTOs):** Chứa toàn bộ Pydantic Request & Response models (`auth.py`, `user.py`, `admin.py`, `flashcard.py`, `review.py`, `vision.py`).
3. **`app/services/` (Pure Business Logic):** Chứa toàn bộ xử lý nghiệp vụ, tính toán thuật toán, truy vấn database, gọi AWS/Redis. Tên hàm ngắn gọn, súc tích (vd: `confirm_card`, `list_cards`, `get_today_queue`, `submit_review`).
4. **`app/routers/` (Thin HTTP Controllers):** Chỉ làm nhiệm vụ nhận HTTP request, parse DTO, kiểm tra dependencies và ủy quyền cho Service tương ứng.

### 2. Thêm một API Mới
1. **Khai báo DTO Schema trong `app/schemas/`** (100% `snake_case`).
2. **Viết nghiệp vụ trong `app/services/`** (thuần logic, testable độc lập).
3. **Khai báo route trong `app/routers/`** (mỏng, ngắn gọn, gọi service).
4. **Đăng ký router trong `app/main.py`** nếu là router mới.

### 3. Sửa Đổi Hoặc Mở Rộng Document Model (MongoDB / Beanie)
1. **Thực hiện trong file tương ứng thuộc `app/models/`:**
   * Document phải kế thừa từ `beanie.Document`.
   * Cung cấp giá trị mặc định (`Field(default=...)` hoặc `Field(default_factory=...)`) cho trường mới.
   * Re-export qua `app/models/__init__.py`.
2. **Đăng ký Document trong Lifespan:**
   * Mở file `app/bootstrap.py` và thêm tên Model mới vào danh sách `document_models` của `init_beanie`.
1. Khởi tạo client Boto3 trong `app/services/ai_service.py` với cấu hình singleton `_get_boto3_session()` và `_BOTO_CONFIG`.
2. **Bắt buộc Non-blocking:** Luôn bọc hàm gọi AWS bằng `await asyncio.to_thread(_sync_call, ...)`.
3. **Quản lý ngoại lệ:** Bắt `ClientError` và chuyển đổi thành lỗi HTTP phù hợp (400 hoặc 502/503), không để văng lỗi 500 ra ngoài router.

---

## 2. Hướng Dẫn Viết Unit Test Chuẩn

Toàn bộ test nằm trong thư mục `tests/` và chạy bằng framework chuẩn `unittest`.

### 1. Quy Tắc Mocking Model Beanie Offline
Do Beanie gắn chặt với kết nối MongoDB, khi viết test unit không có database thật, **TUYỆT ĐỐI KHÔNG** khởi tạo model bằng cú pháp hàm dựng thông thường `User(...)`:
```python
# ❌ SAI: Sẽ ném lỗi CollectionWasNotInitialized
mock_user = User(email="test@example.com")

# ✅ ĐÚNG: Khởi tạo an toàn offline bằng Pydantic v2 model_construct
mock_user = User.model_construct(
    id=uuid.uuid4(),
    email="test@example.com",
    account_tier=AccountTier.FREE,
    preferences=UserPreferences(),
)
```

### 2. Mocking Các Dịch Vụ Bên Ngoài (AWS / Redis / Mongo)
Sử dụng `unittest.mock.patch` hoặc `AsyncMock`:
```python
@patch("app.routers.vision.detect_image_labels", new_callable=AsyncMock)
def test_something(self, mock_detect):
    mock_detect.return_value = VisionResult(...)
    ...
```

### 3. Lệnh Chạy Kiểm Thử
```bash
# Kích hoạt môi trường ảo
source .venv/bin/activate

# Chạy toàn bộ test suite
python -m unittest discover tests
```

---

## 3. Checklist Bắt Buộc Trước Khi Hoàn Thành Task (Pre-Commit Checklist)

Mỗi khi bạn (Developer hoặc AI Agent) hoàn thành một task, hãy tự kiểm tra 5 câu hỏi sau:

- [ ] **1. Naming Check:** Mọi trường mới, API contract mới có đúng 100% `snake_case` không?
- [ ] **2. Test Check:** Đã chạy `python -m unittest discover tests` và 100% test cases đều pass (OK) chưa?
- [ ] **3. Exception Check:** Có khối `except` nào nuốt lỗi im lặng hoặc để lộ stack trace 500 thô thiển không?
- [ ] **4. Documentation Check:** Đã cập nhật file Markdown tương ứng trong thư mục `docs/` để ghi nhận sự thay đổi chưa?
- [ ] **5. Git Command:** Đã dùng lệnh `rtk git <command>` thay vì lệnh `git` trần chưa?
