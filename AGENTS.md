# 🤖 AGENTS.md — Hướng Dẫn Dành Cho AI Agent Tiếp Quản Dự Án

Chào đồng nghiệp Agent! Đây là cẩm nang tóm tắt nhanh giúp bạn nắm bắt toàn bộ ngữ cảnh dự án **LensVocab Backend** trong 60 giây đầu tiên trước khi thực hiện bất kỳ thay đổi nào.

---

## ⚡ 1. Bản Đồ Hệ Thống Nhanh (System Sitemap)

| Thư mục / File | Trách nhiệm chính |
| :--- | :--- |
| [`app/main.py`](file:///home/phong/Môn%20học/app-LensVocab/app/main.py) | Entrypoint FastAPI app, đăng ký router, lifespan quản lý MongoDB & Redis. |
| [`app/models/`](file:///home/phong/Môn%20học/app-LensVocab/app/models) | **Database Entities (Beanie Documents):** `user.py`, `flashcard.py`, `review.py`, `setting.py`. |
| [`app/schemas/`](file:///home/phong/Môn%20học/app-LensVocab/app/schemas) | **API Contracts (Pydantic DTOs):** Request/Response validation (`auth`, `user`, `admin`, `flashcard`, `review`, `vision`). |
| [`app/services/`](file:///home/phong/Môn%20học/app-LensVocab/app/services) | **Pure Business Logic:** `auth_service`, `flashcard_service`, `review_service`, `vision_service`, `ai_service`, `cache_service`, `quota_service`, `sm2_service`, `tier_service`. |
| [`app/routers/`](file:///home/phong/Môn%20học/app-LensVocab/app/routers) | **Thin HTTP Controllers:** Nhận request, xác thực dependency, ủy quyền cho Service và trả response. |
| [`docs/`](file:///home/phong/Môn%20học/app-LensVocab/docs) | **Bộ tài liệu kỹ thuật chi tiết.** Đọc trước khi sửa đổi, cập nhật sau khi hoàn thành. |

---

## 🛑 2. Những Điều CẤM KỴ (Hard Constraints)

1. **KHÔNG dùng `camelCase`:** Dự án tuân thủ nghiêm ngặt 100% `snake_case` ở mọi tầng (từ database field đến JSON response của API).
2. **KHÔNG tạo lại `app/repositories/`:** Sử dụng trực tiếp Beanie Active Record (`Model.find()`, `model.save()`, `model.insert()`).
3. **KHÔNG đổi model embedding tùy tiện:** Model `amazon.titan-embed-text-v2:0` tạo vector 1024 chiều, khớp cứng với `vocab_embedding_index` trong MongoDB Atlas.
4. **KHÔNG gọi `git` trần:** Luôn luôn dùng `rtk git <command>` (ví dụ: `rtk git status`, `rtk git add .`, `rtk git commit`).
5. **KHÔNG khởi tạo `User(...)` trực tiếp trong test unit offline:** Bắt buộc dùng `User.model_construct(...)` để tránh lỗi `CollectionWasNotInitialized` khi chưa kết nối MongoDB.

---

## 📖 3. Quy Trình Khi Sửa Đổi Hoặc Thêm Tính Năng

1. **Đọc tài liệu liên quan trong `docs/`:**
   - Sửa luồng Scan / Cache / AWS AI: Đọc [`docs/02_VISION_AND_CACHE_PIPELINE.md`](file:///home/phong/Môn%20học/app-LensVocab/docs/02_VISION_AND_CACHE_PIPELINE.md).
   - Sửa thuật toán Spaced Repetition: Đọc [`docs/03_SPACED_REPETITION_SM2.md`](file:///home/phong/Môn%20học/app-LensVocab/docs/03_SPACED_REPETITION_SM2.md).
   - Quy trình thêm API mới / chỉnh sửa kiến trúc: Đọc [`docs/04_DEVELOPER_AND_AGENT_GUIDE.md`](file:///home/phong/Môn%20học/app-LensVocab/docs/04_DEVELOPER_AND_AGENT_GUIDE.md).
2. **Viết test trước hoặc song song:** Đảm bảo test case bao phủ cả nhánh thành công và nhánh lỗi (400, 401, 403, 413, 502).
3. **Chạy kiểm thử:**
   ```bash
   .venv/bin/python -m unittest discover tests
   ```
4. **Cập nhật tài liệu trong `docs/`:** Nếu có thay đổi về schema, endpoint, hay luồng nghiệp vụ, phải cập nhật file markdown trong `docs/` tương ứng.
5. **Commit:** Sử dụng `rtk git commit -m "..."`.
