# 04. Cẩm Nang Phát Triển & Mở Rộng Dự Án (Developer & Agent Guide)

Tài liệu này dành cho các kỹ sư backend và AI Agent tiếp quản dự án. Hướng dẫn chi tiết cách mở rộng tính năng, bảo trì mã nguồn và quy chuẩn bắt buộc cần tuân thủ.

---

## 1. Quy Trình Mở Rộng Hệ Thống

### 1. Thêm một API Router Mới
1. **Tạo file router trong thư mục `app/routers/`** (Ví dụ: `app/routers/analytics.py`).
2. **Khai báo Schema Request/Response bằng Pydantic:**
   * Bắt buộc dùng **100% `snake_case`**. Không dùng `camelCase`, không thêm `alias_generator`.
3. **Bảo vệ endpoint:** Luôn dùng `Depends(get_current_user)` nếu endpoint yêu cầu định danh người dùng.
4. **Đăng ký router trong `app/main.py`:**
   ```python
   from app.routers import analytics
   app.include_router(analytics.router, prefix="/api/v1", tags=["Analytics"])
   ```

### 2. Sửa Đổi Hoặc Mở Rộng Document Model (MongoDB / Beanie)
1. **Thực hiện trong `app/models/models.py`:**
   * Document phải kế thừa từ `beanie.Document`.
   * Cung cấp giá trị mặc định (`Field(default=...)` hoặc `Field(default_factory=...)`) cho trường mới để đảm bảo tương thích với các bản ghi cũ trong MongoDB.
2. **Đăng ký Document trong Lifespan:**
   * Mở file `app/bootstrap.py` và thêm tên Model mới vào danh sách `document_models` của `init_beanie`.
3. **Nếu có Index tìm kiếm:**
   * Index đơn: Dùng chuỗi hoặc `Annotated[str, Indexed(...)]`.
   * Index phức hợp (Compound Index): Bắt buộc dùng `IndexModel` của PyMongo:
     ```python
     IndexModel([("field_a", ASCENDING), ("field_b", ASCENDING)], unique=True)
     ```

### 3. Tích Hợp Thêm Dịch Vụ AWS Mới
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
