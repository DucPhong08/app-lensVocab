# 📚 LensVocab Documentation Index (Mục Lục Tài Liệu)

Thư mục `docs/` chứa toàn bộ tài liệu kỹ thuật, kiến trúc, và cẩm nang vận hành của dự án **LensVocab Backend**.

> ⚠️ **Quy tắc vàng dành cho Kỹ sư & AI Agent:**
> Bất kỳ ai sửa đổi logic nghiệp vụ, schema, cấu hình, hoặc API endpoint **BẮT BUỘC** phải cập nhật lại tài liệu tương ứng trong thư mục này để người tiếp quản sau có thể nắm bắt chính xác hệ thống.

---

## 🗂️ Danh Mục Tài Liệu

### 1. [01. Tổng Quan Kiến Trúc & Thiết Kế Dữ Liệu](01_ARCHITECTURE_OVERVIEW.md)
* Mô hình tổng quan hệ thống (FastAPI, MongoDB Atlas, Redis, Pure AWS AI).
* Mô hình **3 Tầng Cấu Hình (3-Tier Configuration)**: `.env` vs `SystemSetting` vs `UserPreferences`.
* Thiết kế phân tầng gói cước: **Free Tier** vs **Premium Tier**.
* Chi tiết các Beanie Document Models: `User`, `GlobalFlashcard`, `UserFlashcard`, `ReviewLog`, `SystemSetting`.

### 2. [02. Chi Tiết Luồng AI Vision & Multi-Tier Cache](02_VISION_AND_CACHE_PIPELINE.md)
* Luồng xử lý chi tiết của endpoint `POST /vision/scan`.
* Cơ chế trích xuất BoundingBox & Label từ **AWS Rekognition**.
* Thuật toán cứu nguy **Graceful Degradation** khi ảnh mờ hoặc góc chụp khó.
* Giải thuật **Multi-tier Cache (4 tầng)**: Redis exact match $\rightarrow$ MongoDB exact match $\rightarrow$ Atlas `$vectorSearch` $\rightarrow$ AWS Bedrock + Polly.
* Xử lý ngoại lệ, bảo vệ quota, và giới hạn payload kích thước ảnh.

### 3. [03. Thuật Toán Lặp Lại Ngắt Quãng SM-2 (Spaced Repetition)](03_SPACED_REPETITION_SM2.md)
* Nguyên lý ghi nhớ dài hạn và đường cong quên lãng Ebbinghaus.
* Công thức toán học tính toán `Interval`, `Repetitions`, và `EFactor`.
* Cơ chế cắt hàng đợi chống nản (**Anti-demotivation Cap**) cho học viên mất gốc.
* Quy trình chấm điểm và ghi log ôn tập (`ReviewLog`).

### 4. [04. Cẩm Nang Phát Triển & Mở Rộng Dự Án (Developer & Agent Guide)](04_DEVELOPER_AND_AGENT_GUIDE.md)
* Quy chuẩn phong cách code (100% `snake_case`, Active Record, non-blocking boto3).
* Hướng dẫn từng bước: Thêm một Router mới, sửa đổi Document Model, thêm tính năng AI mới.
* Hướng dẫn viết Unit Test offline (cách mock model Beanie không cần DB).
* Checklist bắt buộc kiểm tra trước khi hoàn thành task và commit code.
