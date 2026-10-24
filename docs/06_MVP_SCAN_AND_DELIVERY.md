# MVP scan, audio và phân trang — 2026-10-24

Tài liệu này mô tả hành vi hiện tại, thay thế các mô tả cũ về vector search, đa giọng và danh sách trả toàn bộ audio.

## Nhận diện và fallback

- Tách số ứng viên Rekognition (`REKOGNITION_CANDIDATE_LIMIT=30`, tối thiểu 20) khỏi số nhãn được hiển thị (user/admin ceiling). Giảm số nhãn hiển thị xuống 1 không làm mất ngân sách tìm nhãn cụ thể.
- `REKOGNITION_MIN_CONFIDENCE` mặc định 25; cấu hình env cũ nếu có vẫn được tôn trọng. `vision_confidence_threshold` động mặc định 0.5, có thể nhận dạng phân số hoặc phần trăm. Giá trị 1 luôn được hiểu là 100%; muốn 1% dùng 0.01.
- Nhãn bối cảnh/chất liệu như Indoors/Wood/Furniture được hạ ưu tiên, giữ trong raw description để fallback. Food/Person và các danh từ có giá trị học tập không bị cấm chung.
- Nếu chỉ có bối cảnh, không tự chấp nhận đó là vật thể chính. Nếu bối cảnh rỗng, không gọi Bedrock. Người dùng nhận `AI_COULD_NOT_RECOGNIZE` và có thể chụp lại.
- Fallback hiện dùng văn bản nhãn, không gửi ảnh cho Nova. Kết quả được đánh dấu FALLBACK. Không gắn bounding box của vật thể cũ vào từ fallback.
- Mỗi lượt quét tạo một flashcard chính. `detected_objects` là metadata, không phải danh sách thẻ có thể chọn/lưu. UI không quảng bá chọn từng khung. SSE vẫn được hỗ trợ nhưng UI hiện sử dụng REST; không có cam kết số giây tiết kiệm khi chưa đo.

## Snapshot cấu hình

Router đọc và copy một snapshot cấu hình sau khi file hợp lệ. Cùng snapshot được truyền vào quota, giới hạn nhãn và đánh giá confidence. Service gọi độc lập vẫn có thể tự đọc cấu hình khi không được truyền vào.

Không thêm TTL cache theo process. Request tiếp theo đọc Mongo lại và thấy maintenance mới. `_cached_setting` chỉ là fallback khi DB lỗi. Không thay chính sách ngày UTC hay quota nguyên tử.

## IP qua proxy

- `TRUST_PROXY_HEADERS=false` mặc định: mọi forwarded header bị bỏ qua.
- Khi bật, peer kết nối vẫn phải thuộc `TRUSTED_PROXIES` (IP/CIDR). Biến `RENDER` không cấp quyền tin proxy.
- `PROXY_CLIENT_IP_HEADER` mặc định `x-forwarded-for`; chỉ chọn `x-real-ip` hoặc `cf-connecting-ip` nếu chính proxy tin cậy ghi đè header đó.
- XFF được duyệt từ phải sang trái, chỉ đi qua hậu tố proxy tin cậy. Prefix do client tự thêm không được dùng làm IP khách. Header sai định dạng/quá dài từ proxy tin cậy bị từ chối trước khi gọi AWS.
- Cần kiểm tra topology thực tế và cấu hình ingress trước khi bật trust; không tự suy ra CIDR từ môi trường Render hoặc dùng `0.0.0.0/0`.

## Phân trang và âm thanh

`GET /flashcards/page?limit=30&offset=0` trả:

```json
{ "items": [], "total": 0, "next_offset": null }
```

`limit` trong 1–100, `offset` không âm. Thứ tự ổn định theo added_at giảm dần rồi UUID. Trang chỉ đọc các trường văn bản từ GlobalFlashcard, không đọc audio hoặc embedding. `next_offset` tiến theo số thẻ user đã đọc, kể cả khi bản ghi từ vựng chung bị thiếu, để không mắc kẹt phân trang.

Route `/flashcards` giữ dạng mảng cho client cũ nhưng mặc định chỉ 50, tối đa 100 thẻ/request và audio là null. Client phải chuyển sang phân trang/tải audio riêng. `/review/today` cũng không gửi audio base64.

`GET /flashcards/{user_flashcard_id}/audio` trả `{ "audio_base64": "..." }`. Server kiểm tra thẻ thuộc user hiện tại trước khi tra audio. Không thuộc sở hữu/mất thẻ trả 404 `FLASHCARD_NOT_FOUND`; audio chưa có trả 404 `AUDIO_NOT_AVAILABLE`. Endpoint không sinh AI/Polly mới và không cho tải audio qua từ khóa tùy ý.

App tải trang đầu rồi có nút Xem thêm; tổng số từ lấy từ server. Tìm kiếm/bộ lọc tiếp tục tải các trang còn lại theo yêu cầu để tìm đầy đủ kho từ, thay vì khẳng định không có kết quả chỉ từ trang đầu. Ôn tập vẫn dùng hàng đợi riêng, không phụ thuộc trang kho từ đã tải. Khi bấm loa, app tải audio của đúng thẻ; nếu kết quả scan đã có audio thì phát trực tiếp. Đổi tài khoản hủy hiệu lực kết quả đang tải.

## Một giọng chuẩn và tốc độ thực tế

Cả hai gói dùng một giọng theo `POLLY_VOICE_ID` (Joanna mặc định), engine theo `POLLY_ENGINE` (standard mặc định). API công bố đúng một giọng và khả năng engine thực tế. Giọng legacy của user được chuẩn hóa khi đọc preferences; cập nhật mục tiêu/tốc độ không bị chặn bởi cấu hình giọng cũ. Không sửa hàng loạt DB hoặc sinh lại audio cũ.

Tốc độ 0.75 / 1 / 1.25 được lưu trong preferences và áp dụng qua native audio player, không yêu cầu Polly sinh lại. App cung cấp nút chọn tốc độ và báo lỗi khi cập nhật thất bại. Các tệp tạm có tên riêng cho từng player/request để không ghi đè nhau.

## Dọn mã và triển khai

Đã bỏ hàm vector search/Titan không có caller, script tạo vector index và các dependency passlib/Pillow không được app sử dụng. Trường embedding và cấu hình legacy trong dữ liệu được giữ để tránh migration/xóa dữ liệu; không có lời gọi vector trong runtime. Tên env `AWS_EMBEDDING_REGION` tiếp tục chỉ định region Bedrock để không đổi region của deployment đang chạy.

Giữ MongoDB Atlas Local có khả năng transactions. Bước này không thay Docker image, không xóa volume và không chạy migration trên DB thật.

Thứ tự triển khai:

1. Chạy test và kiểm tra Mongo đang hỗ trợ transactions.
2. Triển khai backend trước để có `/flashcards/page` và audio endpoint, kiểm tra mẫu lỗi 401/404/422.
3. Triển khai UI cập nhật ngay sau đó; client cũ không còn nhận toàn bộ audio/danh sách ở một request.
4. Xác minh IP peer/header thực tế và cấu hình proxy allowlist. Nếu env đang ghi đè ngưỡng Rekognition 50 thì cân nhắc hạ về 25 cho nhãn bối cảnh.
5. Smoke test trên thiết bị: guest scan → đăng nhập → lưu → kho từ nhiều trang → bấm loa → đổi tốc độ → ôn tập, và trường hợp maintenance/đổi tài khoản/mất mạng.

Unit test kiểm tra logic và hợp đồng bằng mock. Kiểm thử MongoDB thật và thiết bị native vẫn cần môi trường tương ứng; không coi test mock là đo latency, chi phí hay chất lượng AI thực tế.
