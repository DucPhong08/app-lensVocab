# LensVocab Backend

Backend FastAPI cho ứng dụng học từ vựng tiếng Anh từ ảnh. Mỗi lần quét tạo **một thẻ từ chính**; khách có thể thử quét trước khi đăng nhập.

## Luồng đang hoạt động

1. Kiểm tra MIME, ảnh rỗng và kích thước tối đa 5 MB trước khi trừ quota.
2. Đọc một snapshot cấu hình cho request, kiểm tra maintenance và hạn mức.
3. Rekognition thu thập tối thiểu 30 nhãn ứng viên theo mặc định; số nhãn đầu ra được giới hạn riêng theo user/admin. Ngưỡng thu thập mặc định 25%, ngưỡng chấp nhận mặc định 50%.
4. Ưu tiên danh từ cụ thể. Nhãn bối cảnh được giữ để fallback nhưng không tự trở thành vật thể chính. Không gọi Bedrock khi bối cảnh rỗng; trả `AI_COULD_NOT_RECOGNIZE` để người dùng chụp lại.
5. Tra đúng từ khóa: Redis → MongoDB → Nova Lite và Polly chạy song song. Không có vector search, Titan hay thay từ bằng parent/alias trong luồng chạy.
6. Người dùng xác nhận để lưu thẻ; nội dung dùng bản nháp/cached content do server tạo. Confirm lặp không tạo thêm thẻ trùng.

## API chính

| Method | Endpoint | Hành vi |
| --- | --- | --- |
| POST | `/auth/register`, `/auth/login` | Username/password; email tùy chọn khi đăng ký, hỗ trợ email tài khoản cũ khi đăng nhập; trả access token |
| POST | `/auth/google`, `/auth/google/link` | Đăng nhập Google hoặc liên kết vào tài khoản hiện tại |
| GET | `/auth/me` | Thông tin tài khoản, quota và preferences |
| GET/PATCH | `/users/me/preferences` | Mục tiêu ôn, tốc độ phát trên app, số nhãn hiển thị |
| POST | `/vision/scan/guest` | Quét thử, có quota Redis và cooldown |
| POST | `/vision/scan` | Quét có đăng nhập |
| POST | `/vision/scan/stream` | SSE; UI mobile hiện dùng REST |
| POST | `/flashcards/confirm` | `{ "keyword": "chair" }` |
| GET | `/flashcards/page?limit=30&offset=0` | `{ items, total, next_offset }`; tối đa 100 thẻ/request; không tải audio/embedding |
| GET | `/flashcards/{user_flashcard_id}/audio` | Tải một audio khi bấm nghe; yêu cầu đăng nhập và sở hữu thẻ |
| GET | `/flashcards?limit=50&offset=0` | Mảng legacy có giới hạn, tối đa 100 thẻ; audio là null |
| GET | `/review/today` | Hàng đợi giới hạn theo mục tiêu và gói cước; audio tải riêng |
| POST | `/review/{user_flashcard_id}` | Chấm điểm SM-2; hỗ trợ `review_id` để retry an toàn |
| GET/PATCH | `/admin/settings` | Chỉ admin đang hoạt động |

Giọng đọc MVP là một giọng chuẩn dùng chung (`POLLY_VOICE_ID`, mặc định Joanna). Engine mặc định standard. Không quảng bá đa giọng theo gói. Tốc độ 0.75x / 1x / 1.25x được app áp dụng khi phát, hỗ trợ cả hai gói.

## Chạy local

Yêu cầu Python 3.12 và Docker Compose. Tạo virtualenv, cài `requirements.txt` và cấu hình `.env` theo mẫu; `SECRET_KEY` là bắt buộc.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
docker compose up -d mongodb redis
make dev
```

MongoDB Atlas Local trong Compose được giữ để hỗ trợ replica set/transactions cho ôn tập. Không thay bằng Mongo standalone. Không cần tạo vector index.

Swagger: [http://localhost:8000/api](http://localhost:8000/api).

## Proxy và quota khách

Mặc định `TRUST_PROXY_HEADERS=false`: dùng IP của peer kết nối. Khi chạy sau proxy, phải cấu hình rõ IP/CIDR của proxy trong `TRUSTED_PROXIES`, bật `TRUST_PROXY_HEADERS`, và chọn đúng `PROXY_CLIENT_IP_HEADER` do ingress ghi đè. Không tự động tin header theo biến `RENDER` và không dùng CIDR cho phép mọi nguồn.

Xem [hợp đồng và thứ tự triển khai MVP](docs/06_MVP_SCAN_AND_DELIVERY.md) trước khi triển khai. Chưa cấu hình đúng proxy có thể làm nhiều khách dùng chung quota của một peer.

## Kiểm tra

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover tests
.venv/bin/ruff check --no-cache app tests
```

Kiểm thử MongoDB thật là opt-in, chỉ dùng replica set local có thể xóa bỏ; xem [hướng dẫn kiểm thử](docs/05_OPTIMIZATION_ROLLOUT.md). Unit test mô phỏng AWS/Redis/Mongo, không chứng minh chất lượng nhận diện hay độ trễ thực tế.

## Tài khoản và Google OAuth

Đăng ký bằng `username` + `password`; `email` không bắt buộc. Tài khoản email cũ vẫn đăng nhập được. Đã có `POST /auth/google` và `POST /auth/google/link`; cần OAuth Client ID thật trước khi dùng. Xem [hướng dẫn cấu hình và index migration](docs/07_USERNAME_AND_GOOGLE_AUTH.md).
