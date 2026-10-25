# Chọn nhiều vật và lưu sticker viền trắng

## Luồng đã triển khai

1. Chọn/chụp ảnh JPEG hoặc PNG, tối đa 5 MiB, rồi quét một lần.
2. Màn kết quả khoanh các vật có tọa độ hợp lệ và đủ độ tin cậy. Có thể chạm khung hoặc nút tên vật; số thứ tự phân biệt các vật cùng nhãn.
3. Chọn vật khác chỉ tải từ cho vật đã chọn, không chạy lại Rekognition và không trừ thêm lượt quét. Nội dung từ vẫn đi qua bộ nhớ đệm, từ điển hoặc Bedrock/Polly như trước.
4. Xem trước đúng vùng sẽ xử lý. Khi bấm lưu, backend cắt vùng đã chọn, tách nền bằng U2NetP trên CPU, thêm viền trắng và lưu PNG có alpha lên B2.
5. Thẻ từ, ảnh thu nhỏ và flashcard hiển thị sticker bằng contain, không cắt mất viền. Ảnh toàn cảnh không đặt ở đầu thẻ sticker nữa.
6. Quay lại cùng ảnh để chọn vật khác. Nếu từ đã có trong kho, nút đổi sticker cập nhật ảnh của thẻ hiện có; không tạo thẻ trùng.

## Nhiều vật cùng tên

Backend giữ các instance của mỗi nhãn thay vì chỉ Instances[0]. Các nhãn khác nhau được ưu tiên trước, sau đó bổ sung các instance tiếp theo trong giới hạn max_detected_objects. Tập nhãn ứng viên được giữ để gộp vùng trước khi giới hạn đầu ra. Vùng trùng gần như hoàn toàn (IoU >= 0.85) được gộp để giảm nhãn trùng. Mỗi instance có object_id riêng. Giới hạn của người dùng vẫn bị chặn bởi trần admin; mặc định 5 vật. Trần thấp có thể khiến một số vật không được hiển thị.

## API và bảo vệ lựa chọn

ScanResponse thêm selected_object_id. Mỗi detected_objects item thêm object_id và selection_token. Những trường cũ vẫn được giữ.

- POST /vision/objects/select: Bearer JWT của app; JSON {selection_token}.
- POST /vision/objects/select/guest: JSON tương tự cho token vùng tạo trong lượt quét khách.
- Token vùng do server ký, có hạn 30 phút, chứa tên/vùng được server nhận diện và chủ sở hữu. Không nhận tên tùy ý do client đưa lên để sinh nội dung.
- Token của lượt quét đã đăng nhập chỉ dùng được bởi đúng user đó. Token vùng khách có thể dùng tiếp sau khi đăng nhập, phục vụ luồng dùng thử rồi lưu.
- Token vùng không có sub và không thay thế được JWT đăng nhập. Token sai/hết hạn trả 400 OBJECT_SELECTION_EXPIRED_OR_INVALID, không đăng xuất nhầm phiên app.
- Chế độ bảo trì vẫn chặn bước chọn. Phản hồi cũ bị bỏ qua khi đổi ảnh hoặc phiên đăng nhập.
- Token vùng là thông tin tạm cần giữ riêng: không ghi vào log, URL truy vấn hoặc ảnh chụp debug.

PUT /flashcards/{id}/image thêm form style=sticker (mobile hiện gửi giá trị này), crop vẫn là JSON chuẩn hóa. style=photo vẫn được giữ tương thích client cũ. Response danh sách, ôn tập và ảnh có image_style: photo hoặc sticker. Giá trị suy ra từ object key; không cần migration dữ liệu. File JPG cũ vẫn đọc/xóa được; sticker mới dùng .png và Content-Type image/png.

## Thiết lập backend

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/download_sticker_model.py
```

Các thư viện mới: onnxruntime==1.31.0 và numpy==2.5.4, có wheel Python 3.12 Linux x86_64. Pip check đạt trong môi trường kiểm thử. Không thêm thư viện native mobile.

```dotenv
IMAGE_SEGMENTATION_MODEL_PATH=models/u2netp.onnx
```

Đây cũng là giá trị mặc định. Downloader lấy đúng bản phát hành U2NetP, kiểm tra SHA-256 trước khi cài; runtime kiểm tra lại trước khi nạp mô hình. Không tự tải mô hình trong request. Mô hình khoảng 4.6 MB, được bỏ qua bởi Git. Dockerfile đã thêm bước tải mô hình khi build.

Nếu triển khai Render bằng Native Python, thêm `python scripts/download_sticker_model.py` sau bước cài requirements trong Build Command. Nếu dùng Docker, bước này đã có trong Dockerfile. Giữ cấu hình B2 riêng; không thay khóa AWS. Cần triển khai backend có endpoint mới trước khi phát hành mobile.

Thiếu/sai model trả 503; ảnh khó tách rõ trả 422; máy đang xử lý sticker khác trả 429 STICKER_BUSY. Thẻ từ đã xác nhận vẫn được giữ khi xử lý ảnh thất bại, app báo riêng và cho thử lại. Không âm thầm thay sticker bằng ảnh toàn cảnh.

## Tài nguyên và giới hạn chất lượng

Mỗi tiến trình chỉ cho một job sticker đi qua bước giải mã/tách nền/xuất PNG, kể cả khi request bị hủy. CPU session dùng một thread, tắt bộ nhớ đệm arena và memory pattern; không dùng GPU. Tải model và chạy hai crop cốc/tai nghe trên máy kiểm thử đạt đỉnh RSS khoảng 322 MiB, lần lượt khoảng 0.78 và 0.55 giây cho riêng xử lý cục bộ. Đây không phải thời gian toàn bộ API hay bảo đảm RAM/độ trễ trên Render. Chi phí Rekognition/Bedrock/Polly và hạn mức B2 vẫn áp dụng; không có API tách nền trả phí mới.

U2NetP là mô hình nhẹ. Quai cốc, kính trong, vật mảnh, bóng đổ hoặc nền phức tạp có thể bị lẹm hoặc còn nền bên trong. Phiên bản này chưa có cọ sửa mask. Muốn chất lượng cao hơn cần đánh giá mô hình lớn hơn hoặc xử lý trên thiết bị, kèm đo lại tài nguyên; không mặc định vừa RAM hosting miễn phí.

## Ví dụ đã chạy

Dùng ảnh desk.png có sẵn trong dự án: nhận diện qua Rekognition thật, chọn Coffee Cup rồi Plant trên Android ảo; lưu thành hai sticker PNG riêng có alpha trong B2 thật. Sau lựa chọn và lưu nhiều thẻ, tài khoản vẫn chỉ mất một lượt quét (10 xuống 9). Nghĩa và câu ví dụ dùng từ điển mẫu trong MongoDB thử để tránh gọi thêm Bedrock/Polly trong demo. Môi trường dùng database và Redis tạm riêng; không sử dụng dữ liệu tài khoản thật.

Nguồn: [Rekognition instances](https://docs.aws.amazon.com/rekognition/latest/APIReference/API_Instance.html), [U2Net](https://github.com/xuebinqin/U-2-Net), [nguồn model ONNX U2NetP](https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2netp.py), [ONNX Runtime](https://pypi.org/project/onnxruntime/1.31.0/), [NumPy](https://pypi.org/project/numpy/2.5.4/).


## Kiểm chứng trước tối ưu (24/10/2026)

- Backend: 177/177 test đạt, gồm 10 test MongoDB tạm thật; không bỏ qua test.
- UI: 57/57 test đạt, TypeScript và lint các file thay đổi đạt. Đã khôi phục API origin Render sau demo.
- Backend còn 4 lỗi lint có sẵn (UP042/UP047 ở enum và SM-2); các file mới/sửa không phát sinh lỗi lint. Không coi cổng lint toàn repo là đạt.
- Demo Android dùng Rekognition thật và B2 thật. Hai PNG có alpha, mỗi vật giữ một thẻ; đổi sticker của thẻ cũ vẫn tổng 2 thẻ, ảnh cũ còn 0 phiên bản. Tài khoản mất đúng 1 lượt quét trong app.
- Backend demo chạy thật có VmHWM 393964 KiB (khoảng 385 MiB), RSS sau xử lý khoảng 280 MiB trên máy kiểm thử; không bảo đảm tương tự trên hosting miễn phí.
- Đã đăng xuất demo, xóa hết phiên bản sticker thử trên B2, dừng Android và các database thử riêng. Chưa triển khai lên Render, chưa chạy thiết bị iOS.

## Tối ưu ảnh và lựa chọn (24/10/2026)

- Máy ảnh và thư viện giới hạn cạnh dài ảnh quét ở 1600 px, giữ tỷ lệ, chất lượng JPEG 0.8. Cần kiểm tra lại vật rất nhỏ trên ảnh toàn cảnh trước khi giảm giới hạn thêm.
- Khi lưu một vùng JPEG local có EXIF thường/180°/90°/270°, app cắt đúng tọa độ và thu nhỏ cạnh dài tối đa 768 px trước khi gửi. Không gửi `crop` lần nữa sau khi đã cắt trên thiết bị. File crop tạm được dọn sau upload kể cả khi upload lỗi.
- PNG, JPEG lật gương, metadata chưa đọc được hoặc crop native lỗi giữ đường gửi ảnh và cắt ở backend hiện có. Đây là fallback chủ động để giữ đúng vật; không cam kết mọi định dạng đều giảm cùng mức dung lượng.
- Thẻ được cập nhật ngay vào cache kho từ và các mục ôn tập đã có. Tải lại danh sách chạy ở nền, không giữ nút lưu; lỗi tải nền được thông báo riêng. Tổng số/cursor vẫn dùng giá trị server đến khi đồng bộ, tránh đếm sai thẻ đã có trên trang chưa tải.
- Lựa chọn trong cùng ảnh giữ trong bộ nhớ tối đa 5 phút, theo object_id; đổi ảnh, quét lại, đổi tài khoản hoặc reset sẽ xóa. Không lưu token vùng vào ổ đĩa. Chọn A → B → A → B chỉ gọi API chọn B một lần trong cửa sổ cache.
- Backend gộp tác vụ sinh nội dung/âm thanh cùng từ chuẩn hóa theo event loop và Redis connection pool. HTTP và SSE dùng chung tác vụ. Một client hủy không hủy tác vụ của client khác; lỗi được dọn để lần sau thử lại. Cơ chế này có phạm vi một tiến trình; nhiều worker/instance chưa được gộp liên tiến trình.

### Số đo và giới hạn

- Android x86_64 API 34: bốn JPEG EXIF 1/3/6/8 có kích thước 818083–820213 byte; crop còn 77297–79599 byte (~90% ít byte upload hơn). Pixel sau sửa EXIF đúng kích thước vùng chuẩn backend, sai khác trung bình 0.74–1.23/255 do nén JPEG. Vùng lớn được giới hạn thực tế ở 768×512 px. Đây là số đo fixture local, không phải cam kết tốc độ mạng/Render hay chất lượng trên mọi ảnh.
- Benchmark offline 5 lượt, mỗi lượt 20 yêu cầu cùng từ chưa cache: trước 20 lần sinh nội dung + 20 lần sinh âm thanh; sau 1 + 1. Dịch vụ AI được mô phỏng cùng độ trễ 20 ms để đo số lần gọi, không dùng AWS thật. Độ trễ trung vị 21.83 → 21.38 ms nằm gần nhau; lợi ích đã chứng minh là giảm gọi trùng, không phải tăng tốc AI đơn lẻ.
- Thư viện mới `@react-native-community/image-editor@5.0.0`, MIT, không dependency JavaScript phụ, yêu cầu React Native >=0.77 và New Architecture (app hiện 0.87.1). Tham khảo [tài liệu chính thức](https://github.com/callstack/react-native-image-editor). Cần build lại Android và chạy `pod install` rồi build lại iOS; Metro reload không thêm được module native.
- Android build debug x86_64 và native crop đã kiểm tra. iOS chưa build/chạy trên máy Mac. Npm audit vẫn báo 3 cảnh báo high có sẵn ở body-parser, launch-editor, zod; thư viện cắt ảnh không nằm trong kết quả cảnh báo và lockfile chỉ thêm gói này.

Kiểm chứng bản tối ưu: backend 186/186 test đạt (gồm 10 test MongoDB thật tạm, không skip); UI 79/79 test đạt, TypeScript và ESLint các file thay đổi đạt. Backend strict Ruff vẫn còn 4 lỗi có sẵn ngoài phạm vi sửa; file backend mới/sửa đạt Ruff. Kết quả này thuộc lần tối ưu ban đầu.

## Refactor vòng đời ảnh và trạng thái thẻ (25/10/2026)

- Upload và cập nhật/xóa ảnh được giữ thành một tác vụ có quản lý: client hủy yêu cầu không cắt ngang bước ghi storage/DB. Khi tắt server có trật tự, lifespan đợi cả tác vụ ảnh và tác vụ sinh từ hoàn tất trước khi đóng client; phần dọn tài nguyên nằm trong finally.
- Xóa ảnh đổi image_key bằng compare-and-set trong DB trước, sau đó mới dọn object cũ. Lỗi DB không xóa mất ảnh đang được thẻ tham chiếu. Cập nhật cạnh tranh vẫn trả 409 và chỉ dọn object mới không thắng.
- Khi driver Mongo báo lỗi ghi, backend đọc lại trạng thái. Nếu DB đã ghi thành công nhưng mất acknowledgement, giữ ảnh mới và hoàn tất bình thường. Nếu không đọc được kết quả, trả 503 IMAGE_UPDATE_UNCERTAIN và giữ các ảnh có thể đang được tham chiếu; UI hướng dẫn tải lại thẻ trước khi thử lại.
- Dọn object dùng tối đa 3 lần thử có khoảng chờ, với đúng key thuộc user/card. Nếu storage vẫn lỗi sau 3 lần, ghi cảnh báo phục vụ xử lý vận hành; không xóa ảnh đang được DB tham chiếu để cố bù trừ. Đây là retry trong tiến trình, chưa có hàng đợi dọn ảnh bền vững sau hard kill/mất điện; không cam kết thu gom mọi object khi cả DB/storage mất kết nối kéo dài.
- UI giữ thẻ vừa lưu theo tài khoản khi trang đầu chưa tải được, hợp nhất không trùng khi server đồng bộ. Phản hồi trang cũ không ghi đè ảnh vừa lưu. Lịch ôn cập nhật cả thẻ đang chờ; tổng số và cursor vẫn do server cung cấp.
- Kho từ vẫn hiển thị những thẻ đã có khi tải nền lỗi/chưa xong, kèm nút tải lại. Lỗi cập nhật cache local không bị báo nhầm thành lỗi lưu server.
- Trạng thái sticker đã lưu dựa vào object_id + ảnh + tài khoản, thay cho tham chiếu object. Chọn A → B → A giữ đúng trạng thái. Hai vùng cùng từ vẫn dùng một thẻ; thay ảnh thành công sẽ thay liên kết cũ, thay ảnh thất bại giữ liên kết đã lưu trước đó và cho thử lại sticker mới.
- HTTP và SSE dùng chung nhận diện/vùng/metadata và tra cache/sinh từ. SSE bổ sung selected_object_id; tên event và các trường cũ được giữ. Không thay quota hoặc lược đồ DB.
- Chuẩn hóa ba enum sang StrEnum và generic SM-2 theo Python 3.12, xử lý hết 4 lỗi Ruff trước đó.

Kiểm chứng: cổng QA backend chính thức đạt Ruff toàn repo và 200/200 test, gồm 10 test MongoDB local thật, không skip. UI đạt 90/90 test, TypeScript và ESLint không có lỗi. ESLint toàn UI còn 3 warning có sẵn ở bài test API và logic tạo review_id. npm audit còn 3 cảnh báo high có sẵn ở body-parser/launch-editor (công cụ React Native CLI) và zod (công cụ lint); thư viện cắt ảnh không bị báo trong kết quả audit. Không nâng các gói ngoài phạm vi thay đổi này. Android native crop/build đã kiểm tra ở lần tối ưu trước; iOS chưa build/chạy trên máy Mac.
