# Kiến trúc LensVocab hiện tại

Xem [hợp đồng MVP](06_MVP_SCAN_AND_DELIVERY.md) và [các bảo đảm dữ liệu](05_OPTIMIZATION_ROLLOUT.md) cho chi tiết và thứ tự triển khai.

## Thành phần

- **FastAPI:** router mỏng, schema Pydantic, service xử lý nghiệp vụ, Beanie Active Record. Không có tầng repository bọc rỗng.
- **MongoDB:** users, global_flashcards, user_flashcards, review_logs, system_settings. Quota user được reset/trừ trong một atomic update. Chấm điểm thẻ và ghi log dùng transaction; cần replica set/Atlas.
- **Redis:** cache exact keyword, thẻ nháp, quota/cooldown khách qua Lua. Không là nguồn dữ liệu bền vững của quota user.
- **AWS:** Rekognition chọn nhãn; Nova Lite tạo nội dung và fallback từ bối cảnh; Polly tạo audio một giọng chuẩn. Các lời gọi boto3 chạy trong worker thread, client dùng lại trong process và đóng lúc shutdown.
- **Mobile:** React Native dùng REST; tải kho từ theo trang và audio khi bấm nghe. SSE là khả năng backend chưa được mobile sử dụng.

## Luồng dữ liệu

```text
Ảnh → kiểm tra file → một snapshot settings → quota
     → Rekognition (ngân sách ứng viên riêng, ưu tiên danh từ)
     → chấp nhận / fallback có bối cảnh / yêu cầu chụp lại
     → Redis exact → Mongo exact → Nova + Polly
     → thẻ chính → confirm → UserFlashcard

Kho từ → trang văn bản + tổng số → audio riêng khi bấm loa
Ôn tập → hàng đợi có giới hạn → SM-2 + ReviewLog trong transaction
```

## Cấu hình và quyền

`.env` quản lý hạ tầng, bí mật và AWS. SystemSetting quản lý giới hạn/maintenance động. UserPreferences quản lý mục tiêu, tốc độ phát và số nhãn đầu ra. Một request dùng một snapshot SystemSetting; request kế tiếp thấy cập nhật mới.

FREE/PREMIUM khác nhau về quota và giới hạn học. MVP không bán quyền đa giọng chưa hoạt động. Voice/engine dùng cấu hình chung; tốc độ áp dụng trên app. Admin yêu cầu is_admin, không suy ra từ PREMIUM.

## Dữ liệu legacy

Không chạy vector search hoặc Titan trong pipeline. Trường embedding nullable và một số tên cấu hình legacy được giữ để không thay dữ liệu/region của deployment hiện có. Chúng không thể hiện tính năng tìm tương đồng đang hoạt động. Không cần tạo vector index khi cài mới.
