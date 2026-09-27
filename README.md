# 📸 LensVocab Backend API

> **Hệ thống Backend học từ vựng tiếng Anh qua hình ảnh sử dụng Pure AWS AI & Thuật toán lặp lại ngắt quãng (SM-2 Spaced Repetition).**

---

## 🌟 Giới thiệu ngắn gọn

**LensVocab** biến camera thành công cụ học từ vựng trực quan:

1. **Chụp ảnh đồ vật:** Hệ thống nhận diện thực thể, vẽ khung viền (BoundingBox theo phong cách Google Lens).
2. **Sinh Flashcard tức thì:** Tự động tra cứu qua **Multi-tier Cache**; nếu là từ mới hoàn toàn, AWS Bedrock (Nova Lite) sẽ sinh phiên âm IPA, giải nghĩa tiếng Việt, 2 câu ví dụ thực tế và AWS Polly sinh file phát âm MP3 chuẩn bản ngữ.
3. **Cơ chế cứu nguy (Graceful Degradation):** Nếu ảnh mờ hoặc góc chụp khó (Rekognition confidence < 50%), Bedrock sẽ phân tích ngữ cảnh ảnh để gợi ý từ phù hợp nhất.
4. **Ghi nhớ dài hạn (SM-2):** Tự động lên lịch ôn tập ngắt quãng dựa trên độ nhớ của học viên, đi kèm trần ôn tập hàng ngày (Anti-demotivation Cap) giúp người mất gốc không bị quá tải.

---

## 🛠️ Tech Stack

- **Framework:** FastAPI (Python 3.12, Async/Await)
- **Database & ODM:** MongoDB Atlas Local / Cloud M0 (`beanie==1.26.0`, `motor==3.6.0`)
- **Vector Search:** MongoDB Atlas `$vectorSearch` (Cosine similarity, 1024 dimensions)
- **Cache & Quota:** Redis 7 Alpine (Atomic rate-limiting, key TTL, LRU cache)
- **Bảo mật & Auth:** Native `bcrypt` + JWT Bearer Token (`python-jose`)
- **AWS Cloud AI Suite (Pure AWS):**
  - **AWS Rekognition:** Nhận diện vật thể, nhãn, tọa độ BoundingBox.
  - **AWS Bedrock (Nova Lite):** Sinh nội dung flashcard và fallback bối cảnh.
  - **AWS Bedrock (Titan Text Embeddings v2):** Vector hóa từ vựng (1024 dims).
  - **AWS Polly:** Text-to-Speech phát âm bản ngữ (Neural Engine).

---

## 🏛️ Kiến trúc cốt lõi

```
Client (Camera)
   │
   ▼
FastAPI [/api/v1/scan] ──► [Redis Quota & Maintenance Gate]
   │
   ├─► 1. AWS Rekognition (Detect Labels & Bounding Box)
   │      └─► Confidence < 50%? ──► Bedrock Graceful Fallback
   │
   └─► 2. Multi-Tier Cache Resolver:
          ├─► Tier 1: Redis Exact Match (Cost = $0, < 5ms)
          ├─► Tier 2a: MongoDB Exact Match (Cost = $0, < 15ms)
          ├─► Tier 2b: MongoDB Atlas $vectorSearch (Titan v2 Embeddings)
          └─► Tier 3: AWS Bedrock Nova Lite + AWS Polly TTS (Cache Miss)
```

- **Chính sách gói cước (Free vs Premium):**
  - **Free:** 10 lượt scan/ngày, trần ôn tập 15–20 từ/ngày, giọng đọc chuẩn `Joanna`.
  - **Premium:** 200 lượt scan/ngày, mở khóa 5 giọng đọc (US/UK/AU: Joanna, Matthew, Amy, Brian, Olivia), tùy chỉnh tốc độ đọc (0.75x, 1.0x, 1.25x), mở khóa giọng Neural tự nhiên.
- **Cấu hình động (Dynamic System Settings):** Admin có thể thay đổi hạn mức quota, ngưỡng tin cậy AI, hoặc kích hoạt chế độ bảo trì toàn hệ thống qua API/DB mà **không cần restart server**.

---

## 🚀 Hướng dẫn cài đặt & Khởi chạy

### 1. Yêu cầu môi trường

- Python 3.12+
- Docker & Docker Compose
- MongoDB Shell (`mongosh`)

### 2. Cài đặt dự án

```bash
# Clone repository
git clone https://github.com/DucPhong08/app-lensVocab.git
cd app-lensVocab

# Khởi tạo môi trường ảo Python
python3 -m venv .venv
source .venv/bin/activate

# Cài đặt thư viện dependencies
pip install -r requirements.txt
```

### 3. Cấu hình biến môi trường

Tạo file `.env` từ file mẫu:

```bash
cp .env.example .env
```

### 4. Khởi chạy Database & Cache (Docker)

Chạy MongoDB Atlas Local và Redis qua Docker Compose:

```bash
docker compose up -d mongodb redis
```

### 5. Tạo Vector Search Index (Chạy 1 lần duy nhất)

Sau khi MongoDB khởi động, chạy file script để tạo Search Index cho Vector Embedding (1024 chiều):

```bash
mongosh "mongodb://root:root@localhost:27017/?authSource=admin" create_vector_index.js
```

### 6. Khởi động API Server

Cách nhanh nhất (khuyên dùng):
```bash
make dev
```

Hoặc chạy lệnh trực tiếp bằng uvicorn:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Swagger UI Documentation:** [http://localhost:8000/api](http://localhost:8000/api)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 🧪 Chạy Kiểm Thử (Unit Tests)

Dự án bao gồm bộ 30 unit tests tự động, mock toàn bộ dịch vụ ngoài (AWS, MongoDB, Redis):

```bash
make test
# hoặc: .venv/bin/python -m unittest discover tests
```

---

## 📋 Danh sách API Endpoints chính

| Nhóm          | Method  | Endpoint                 | Mô tả                                                            |
| :------------ | :------ | :----------------------- | :--------------------------------------------------------------- |
| **Auth**      | `POST`  | `/auth/register`         | Đăng ký tài khoản mới & nhận JWT Token                           |
|               | `POST`  | `/auth/login`            | Đăng nhập tài khoản                                              |
|               | `GET`   | `/auth/me`               | Lấy thông tin cá nhân & hạn mức scan còn lại                     |
| **Users**     | `GET`   | `/users/me/preferences`  | Xem sở thích học tập & quyền lợi gói cước                        |
|               | `PATCH` | `/users/me/preferences`  | Cập nhật giọng đọc Polly, tốc độ phát âm, mục tiêu học           |
| **Vision**    | `POST`  | `/vision/scan`           | Tải ảnh lên nhận diện đồ vật, trích xuất BoundingBox & Flashcard |
| **Flashcard** | `POST`  | `/flashcards/confirm`    | Xác nhận lưu thẻ vào bộ sưu tập cá nhân                          |
|               | `GET`   | `/flashcards`            | Xem danh sách thẻ từ vựng đã lưu                                 |
| **Review**    | `GET`   | `/review/today`          | Lấy hàng đợi từ cần ôn tập hôm nay (SM-2)                        |
|               | `POST`  | `/review/submit`         | Gửi đánh giá kết quả ôn tập (chấm điểm chất lượng 0 - 5)         |
| **Admin**     | `GET`   | `/admin/settings`        | Xem cấu hình động của hệ thống                                   |
|               | `PATCH` | `/admin/settings`        | Cập nhật hạn mức quota, ngưỡng AI, bật/tắt bảo trì               |

---
