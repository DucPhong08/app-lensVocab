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


## Kiểm chứng cuối

- Backend: 177/177 test đạt, gồm 10 test MongoDB tạm thật; không bỏ qua test.
- UI: 57/57 test đạt, TypeScript và lint các file thay đổi đạt. Đã khôi phục API origin Render sau demo.
- Backend còn 4 lỗi lint có sẵn (UP042/UP047 ở enum và SM-2); các file mới/sửa không phát sinh lỗi lint. Không coi cổng lint toàn repo là đạt.
- Demo Android dùng Rekognition thật và B2 thật. Hai PNG có alpha, mỗi vật giữ một thẻ; đổi sticker của thẻ cũ vẫn tổng 2 thẻ, ảnh cũ còn 0 phiên bản. Tài khoản mất đúng 1 lượt quét trong app.
- Backend demo chạy thật có VmHWM 393964 KiB (khoảng 385 MiB), RSS sau xử lý khoảng 280 MiB trên máy kiểm thử; không bảo đảm tương tự trên hosting miễn phí.
- Đã đăng xuất demo, xóa hết phiên bản sticker thử trên B2, dừng Android và các database thử riêng. Chưa triển khai lên Render, chưa chạy thiết bị iOS.
