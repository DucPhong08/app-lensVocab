# Lưu ảnh cá nhân trên flashcard

## Hành vi đã triển khai

- Quét ảnh vẫn nhận diện như trước. Ảnh chưa được đưa lên kho lưu trữ khi chỉ quét hoặc dùng thử.
- Khi bấm lưu, app xác nhận thẻ từ trước, rồi gửi ảnh của đúng lượt quét vào endpoint của thẻ đã sở hữu. Luồng khách → đăng nhập bằng username/Google → tiếp tục lưu vẫn được giữ.
- Ảnh được lưu riêng theo tài khoản và thẻ (`UserFlashcard.image_key`), không đặt ảnh cá nhân vào `GlobalFlashcard` dùng chung. Hai người học cùng từ chair có thể dùng hai ảnh khác nhau.
- Khi kết quả có bounding box của vật thể chính, backend cắt vùng đó. Kết quả FALLBACK hoặc không có box dùng toàn ảnh để tránh cắt nhầm đối tượng.
- Ảnh JPEG/PNG tối đa 5 MiB, tối đa 20 triệu pixel trước giải mã. Backend kiểm tra ảnh thực, sửa chiều xoay EXIF, cắt ảnh, thu nhỏ tối đa 1600×1600 giữ tỷ lệ và xuất JPEG chất lượng 85. Alpha được ghép nền trắng. Không lưu EXIF/GPS, XMP, comment hay ICC của ảnh gốc.
- Ảnh hiện trong danh sách đã lưu, chi tiết từ và mặt trước flashcard ôn tập. Chi tiết từ có Thêm ảnh/Đổi ảnh/Xóa ảnh; xóa ảnh cần xác nhận và giữ nguyên từ cùng tiến độ ôn.
- S3 để private. Server lưu object key và cấp URL GET có chữ ký hết hạn (mặc định 1 giờ), không lưu URL có hạn vào MongoDB. App có thể xin URL mới khi ảnh tải lỗi.
- Nếu kho ảnh chưa cấu hình hoặc upload thất bại, thẻ từ vẫn được lưu. App báo riêng việc ảnh chưa lưu, và người dùng có thể mở chi tiết thẻ để thêm lại.

## Backblaze B2 (bucket đã tạo)

Backend hỗ trợ B2 qua S3-compatible API; endpoint B2 dùng khóa riêng, không thay các AWS IAM key của Rekognition/Bedrock/Polly. Endpoint chỉ chấp nhận HTTPS theo dạng `https://s3.<region>.backblazeb2.com`.

```dotenv
S3_IMAGE_BUCKET=lenvocab-images-phong-20261024
S3_IMAGE_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com
S3_IMAGE_ACCESS_KEY_ID=YOUR_B2_KEY_ID
S3_IMAGE_SECRET_ACCESS_KEY=YOUR_B2_APPLICATION_KEY
S3_IMAGE_URL_TTL_SECONDS=3600
```

- Trong B2 → Application Keys, tạo khóa Read and Write chỉ cho bucket này, có thể giới hạn prefix `user-images/`. Không dùng master key, không cần liệt kê mọi bucket.
- Hai khóa phải có đủ khi dùng endpoint B2; thiếu khóa trả `IMAGE_STORAGE_NOT_CONFIGURED`, không dùng nhầm AWS credentials hoặc metadata credentials.
- Region được lấy từ endpoint (`us-east-005`); `S3_IMAGE_REGION` chỉ dùng cho AWS khi endpoint trống. Khởi động lại backend sau khi đổi cấu hình.
- Client B2 dùng path-style URLs, SigV4 và chỉ tính checksum khi bắt buộc để tránh checksum trailer tùy chọn không tương thích. SSE `AES256` bật mã hóa SSE-B2 trên B2.
- B2 giữ phiên bản. Khi đổi/xóa/dọn ảnh thất bại, backend liệt kê phiên bản bằng prefix đầy đủ của object, lọc đúng key, rồi xóa từng VersionId và delete marker. Không chỉ gửi DELETE không VersionId vì thao tác đó chỉ thêm hide marker. Không xóa object khác dù có cùng prefix.
- Endpoint trống giữ cách dùng AWS S3 cũ; chuyển provider không tự di chuyển ảnh đã có. Bucket hiện mới và trống theo ảnh người dùng cung cấp.
- Không commit `.env`, không đưa B2 key vào app mobile. Không đặt `AWS_ENDPOINT_URL` toàn cục thành B2 endpoint.
- Chưa upload/đọc/xóa trên B2 thật; cần người vận hành nhập khóa và thử app sau khi khởi động lại backend.

Nguồn: [Boto3 với B2](https://www.backblaze.com/docs/cloud-storage-use-the-aws-sdk-for-python-with-backblaze-b2), [DELETE và VersionId](https://www.backblaze.com/apidocs/s3-delete-object), [PUT encryption](https://www.backblaze.com/apidocs/s3-put-object).

## Cấu hình AWS S3 thay thế

1. Trong AWS Console → S3 → Create bucket, tạo một bucket riêng cho ảnh LenVocab. Ghi lại bucket name và region.
2. Giữ **Block all public access** bật. Giữ Object Ownership **Bucket owner enforced** (ACLs disabled). Không cấp public-read cho bucket hoặc object.
3. Dùng mã hóa SSE-S3. Backend gửi `ServerSideEncryption=AES256` và `ContentType=image/jpeg` khi upload.
4. Cấp cho role/user IAM của backend quyền dưới đây, thay `YOUR_PRIVATE_BUCKET` bằng tên thật. Ưu tiên role IAM của môi trường triển khai; không đưa AWS key vào app mobile.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
    "Resource": "arn:aws:s3:::YOUR_PRIVATE_BUCKET/user-images/*"
  }]
}
```

Không cần `s3:PutObjectAcl` hoặc quyền tạo/xóa bucket cho runtime. Đường dẫn ảnh do server tạo, không nhận object key hay URL tùy ý từ client.

5. Thêm cấu hình vào môi trường backend (không commit `.env` thật):

```dotenv
S3_IMAGE_BUCKET=YOUR_PRIVATE_BUCKET
S3_IMAGE_REGION=ap-southeast-1
S3_IMAGE_URL_TTL_SECONDS=3600
```

Region phải là region của bucket. Để trống `S3_IMAGE_REGION` sẽ dùng `AWS_REGION`. Dùng AWS credentials/role đã cấu hình cho backend; cần bổ sung quyền S3 nêu trên. Bucket rỗng nghĩa là tính năng upload ảnh tắt, không tự tạo bucket hoặc cấp public access.

6. Cài requirements backend (đã thêm `Pillow==13.0.0`), triển khai backend trước rồi cập nhật UI. Không cần thêm native package cho ảnh trong mobile; dùng image picker có sẵn. Mobile không có AWS credentials.

## Hợp đồng API

| Endpoint | Quyền và dữ liệu | Kết quả |
| --- | --- | --- |
| PUT `/flashcards/{user_flashcard_id}/image` | Bearer JWT; multipart `file`, `crop?` là JSON `{left,top,width,height}` trong khoảng chuẩn hóa 0–1 | `{image_url, has_image}` |
| GET `/flashcards/{user_flashcard_id}/image` | Bearer JWT, thẻ thuộc user hiện tại | URL mới có hạn; không có ảnh trả 404 |
| DELETE `/flashcards/{user_flashcard_id}/image` | Bearer JWT, thẻ thuộc user hiện tại | Xóa ảnh, trả `{image_url:null, has_image:false}`; thẻ đã không có ảnh thì thao tác vẫn thành công |

Danh sách flashcard, confirm và hàng đợi ôn bổ sung `image_url` nullable và `has_image`. Không nhúng byte/base64 ảnh trong danh sách. Trường `image_key` không được xuất trong response.

Lỗi: 401 chưa đăng nhập; 404 `FLASHCARD_NOT_FOUND` nếu không sở hữu; 404 `IMAGE_NOT_AVAILABLE` nếu ảnh không có/không hợp lệ; 400 `INVALID_IMAGE_DATA`/`INVALID_IMAGE_FORMAT`/`INVALID_CROP_BOX`; 413 `IMAGE_TOO_LARGE`/`IMAGE_PIXEL_LIMIT_EXCEEDED`; 503 `IMAGE_STORAGE_NOT_CONFIGURED`/`IMAGE_STORAGE_UNAVAILABLE`; 409 `IMAGE_UPDATE_CONFLICT` khi thẻ vừa được cập nhật bởi yêu cầu khác.

## Tính nhất quán và quyền riêng tư

- Kiểm tra quyền sở hữu trước khi giải mã hoặc thao tác S3. Mỗi object nằm dưới `user-images/{user_id}/{card_id}/{random_id}.jpg`. Không dùng filename của client để tạo đường dẫn.
- Thay ảnh dùng cập nhật Mongo có điều kiện theo object key cũ. Nếu hai yêu cầu cùng thay từ một trạng thái, chỉ một yêu cầu thắng; ảnh mới không được tham chiếu của yêu cầu thua được dọn.
- Sau khi thay thành công, ảnh cũ được xóa. Nếu ghi Mongo thất bại, backend cố gắng xóa ảnh mới. Không xóa object ngoài prefix của đúng chủ sở hữu/thẻ.
- S3 và Mongo không có transaction chung. Nếu process chết giữa upload và ghi Mongo, hoặc xóa S3 thất bại, có thể có object mồ côi. Log `card_image_cleanup_failed` cần được theo dõi và đối chiếu khi vận hành; không áp lifecycle xóa toàn bộ `user-images/` theo tuổi vì sẽ xóa cả ảnh đang được dùng.
- URL ký sẵn là quyền đọc tạm thời cho người có URL; không chia sẻ hoặc ghi URL vào log. URL có thể hết hạn sớm hơn nếu credentials ký hết hạn. Đăng xuất app không thu hồi một URL đã cấp; xóa ảnh chặn các lượt đọc mới từ S3, nhưng không thể thu hồi bản sao người khác đã tải.
- App không nhận kết quả tải/đổi ảnh từ phiên cũ sau khi đổi tài khoản; xác nhận xóa cũ cũng không được dùng để xóa ảnh của phiên trước.

## Kiểm tra sau khi có bucket

1. Đăng ký username không email → quét → lưu → mở lại kho từ/chi tiết/flashcard và kiểm tra ảnh vẫn còn sau khi mở app lại.
2. Dùng khách quét rồi đăng nhập → lưu, kiểm tra ảnh của đúng lượt quét, kể cả mở lại một ảnh trong lịch sử phiên.
3. Hai user lưu cùng từ với hai ảnh khác nhau; user B không được tải/đổi/xóa ảnh thuộc thẻ user A.
4. Đổi ảnh, xóa ảnh có xác nhận; thẻ và tiến độ ôn không thay đổi.
5. Ảnh xoay EXIF, PNG trong suốt, ảnh sai dữ liệu, ảnh quá lớn, mất mạng, S3 thiếu quyền và URL hết hạn.
6. Khi không có `S3_IMAGE_BUCKET`, lưu từ vẫn thành công và app báo ảnh chưa lưu.

Người dùng đã tạo bucket B2, chưa cấu hình khóa cho backend. Kiểm thử tự động dùng ảnh tạo trong test, client giả/SDK Stubber và MongoDB tạm ở đợt triển khai trước; không tải ảnh hoặc tạo tài nguyên trên AWS/B2 thật.

Nguồn: [Rekognition orientation](https://docs.aws.amazon.com/rekognition/latest/APIReference/API_DetectLabels.html), [S3 presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html), [Boto3 presigned URLs](https://docs.aws.amazon.com/boto3/latest/guide/s3-presigned-urls.html), [Pillow image limits](https://pillow.readthedocs.io/en/stable/reference/Image.html), [EXIF transpose](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html).
