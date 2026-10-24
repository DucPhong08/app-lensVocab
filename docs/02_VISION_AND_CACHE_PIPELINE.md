# Pipeline nhận diện và cache

Tài liệu chi tiết: [MVP scan và delivery](06_MVP_SCAN_AND_DELIVERY.md).

## Quét REST

1. Router xác thực (trừ guest), kiểm tra loại ảnh/rỗng/5 MB trước quota.
2. Đọc một snapshot SystemSetting, kiểm tra maintenance và khấu trừ quota nguyên tử.
3. Rekognition lấy tập ứng viên rộng, độc lập với số nhãn đầu ra được user/admin cho phép.
4. Ưu tiên danh từ cụ thể trước nhãn bối cảnh; giữ bối cảnh cho fallback. Không có dữ liệu thì không gửi prompt rỗng cho Bedrock.
5. Khi có từ chính: exact Redis → exact Mongo → sinh Nova và Polly song song. Parent/alias chỉ là metadata, không thay thế từ được chọn.
6. Nội dung AI qua kiểm tra schema trước khi cache; lỗi sinh nội dung được retry giới hạn và trả lỗi chuẩn khi hết lượt.
7. Cache draft ở vocab:{keyword} với TTL. Confirm chỉ dùng nội dung server tạo, trả DRAFT_EXPIRED nếu không còn nội dung tương ứng. Không gọi thêm AI trong confirm.

Mỗi lần quét tạo một thẻ chính. Khi fallback chọn từ bối cảnh, không dùng lại bounding box của vật thể khác. UI không cho chọn các nhãn phụ như những flashcard đã được chuẩn bị.

## SSE

SSE trả trạng thái, kết quả nhận diện, nội dung và audio theo các bước xử lý. Nhánh fallback có thể chờ Bedrock trước khi có kết quả nhận diện cuối; nội dung/audio cache miss hiện chờ hai tác vụ song song hoàn tất. Không công bố thời gian tiết kiệm cố định khi chưa đo. Mobile hiện dùng REST.

## Audio và danh sách

Một voice/engine chuẩn cho mọi user. Audio được lưu một bản theo từ vựng chung; không có cache theo giọng cá nhân. API trang kho từ và hàng đợi ôn không đọc/trả blob audio hoặc embedding. Endpoint audio riêng kiểm tra quyền sở hữu thẻ; tốc độ đọc áp dụng trên native player.

## Lỗi và nguồn dữ liệu

Redis lỗi chặn scan trước lời gọi AWS. Guest quota dùng Lua và IP từ peer/proxy được cấu hình rõ. Quota user dùng Mongo làm nguồn bền vững. File sai trả 400/413; hết quota user trả 403; guest rate limit trả 429; maintenance/Redis lỗi trả 503; AWS lỗi trả 502 hoặc SSE error event.
