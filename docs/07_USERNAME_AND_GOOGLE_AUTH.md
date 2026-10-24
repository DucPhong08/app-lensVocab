# Username và đăng nhập Google

## Hành vi hiện tại

- Tạo tài khoản chỉ cần username + mật khẩu. Username không phân biệt hoa/thường, dài 3–30 ký tự, bắt đầu bằng chữ/số; phần còn lại cho phép chữ ASCII, số, dấu chấm và gạch dưới.
- Email và tên hiển thị không bắt buộc. Email nhập tay chưa được xác minh và không được dùng làm bằng chứng sở hữu tài khoản Google.
- Tài khoản email cũ vẫn đăng nhập bằng email + mật khẩu. Không đổi ID, flashcard hay lịch ôn. Đăng ký mới cần username; client cũ chỉ gửi email cần cập nhật.
- Nút **Tiếp tục với Google** lấy ID token bằng SDK rồi gửi backend. Backend xác minh chữ ký, issuer, audience và hạn dùng trước khi cấp JWT của LenVocab. App chỉ lưu JWT LenVocab vào Keychain, không lưu Google ID token hoặc refresh token.
- Google mới được gán username `g_...`, giữ tên hiển thị Google nếu có. Không có mật khẩu cục bộ tự tạo.
- Tài khoản được nhận diện bằng Google `sub`, không bằng email. Email trùng tài khoản cũ trả `GOOGLE_ACCOUNT_LINK_REQUIRED`: đăng nhập bằng mật khẩu, vào Cá nhân → Liên kết Google. Không tự gộp hoặc chuyển dữ liệu.
- Liên kết yêu cầu phiên LenVocab hợp lệ và ID token Google hợp lệ, dùng cập nhật có điều kiện và unique index để tránh liên kết đè/chiếm tài khoản. Hiện không có chức năng gỡ liên kết hay tự đặt mật khẩu cho tài khoản Google.
- Khi hủy cửa sổ chọn tài khoản, app giữ màn hình đăng nhập. Khi chưa cấu hình Client ID, app báo chưa thiết lập; username vẫn dùng được.

## 1. Tạo OAuth Client trong Google Cloud

1. Mở [Google Cloud Console](https://console.cloud.google.com/), chọn/tạo project.
2. Trong Google Auth Platform, cấu hình Branding, Audience và thông tin liên hệ. Khi ứng dụng ở Testing, thêm các tài khoản dùng thử vào Test users.
3. Tạo OAuth Client loại **Web application** cho backend. Sao chép Client ID có đuôi `.apps.googleusercontent.com`. Native SDK gửi token có audience là ID này. Không đặt Web client secret vào app; luồng ID token này không cần secret.
4. Tạo OAuth Client loại **Android**, package name `com.lenvocab.app`, SHA-1 của chứng chỉ ký app. Lấy SHA-1 bằng `cd android && ./gradlew signingReport`. Khi phát hành, thêm client phù hợp với chứng chỉ Play App Signing; debug và release có thể dùng các client khác nhau.
5. Nếu chạy iOS, tạo OAuth Client loại **iOS** với bundle ID `com.lenvocab.app`, ghi lại iOS Client ID và iOS URL scheme (reversed client ID).

Không cần Firebase cho luồng này. Android Client ID dùng để Google nhận diện package/chứng chỉ, không thay cho Web Client ID trong cấu hình sau.

## 2. Cấu hình backend

Cài requirements rồi thêm vào môi trường backend:

```dotenv
GOOGLE_WEB_CLIENT_ID=YOUR_WEB_CLIENT_ID.apps.googleusercontent.com
```

Để trống nghĩa là Google bị tắt; `/auth/google` trả 503 `GOOGLE_AUTH_NOT_CONFIGURED`. Không có đường bỏ qua kiểm tra token trong môi trường dev.

Khi backend khởi động, nó tạo unique partial indexes cho `email`, `username`, `google_sub`, chỉ áp dụng với giá trị kiểu string. Sau khi tạo thành công, nó thay index email mặc định cũ `email_1` bằng index mới để nhiều tài khoản có thể không có email. Không xóa document hoặc đổi ID người dùng. Nếu tạo constraint thất bại do dữ liệu trùng, startup dừng; không tự sửa/xóa dữ liệu trùng. Nên có backup và khởi động backend mới trước khi triển khai UI; giữ quyền create/drop index cho bước cập nhật này. Không rollback về backend yêu cầu email trước khi xử lý các tài khoản mới thiếu email.

Việc viết code không chạy migration trên database thật. Bộ test offline không xác nhận migration đã chạy trên Atlas của bạn.

## 3. Cấu hình mobile

Điền **Client ID công khai** vào `src/services/auth/googleConfig.ts`:

```ts
export const GOOGLE_AUTH_CONFIG = {
  webClientId: 'YOUR_WEB_CLIENT_ID.apps.googleusercontent.com',
  iosClientId: 'YOUR_IOS_CLIENT_ID.apps.googleusercontent.com',
};
```

Web Client ID phải trùng `GOOGLE_WEB_CLIENT_ID` ở backend. Android không cần iosClientId. iOS cần cả hai trong cách cấu hình này.

Sau khi cài dependencies, rebuild app native; chỉ reload Metro không đủ. Android dùng autolinking. SDK miễn phí đang dùng Original Google Sign-In; nhà phát triển thư viện ghi rõ SDK Android nền đã deprecated nhưng vẫn hoạt động. Khi lên kế hoạch phát hành dài hạn, đánh giá chuyển sang Credential Manager (Universal edition của thư viện là sản phẩm khác), không coi bản MVP là bảo đảm hỗ trợ Android lâu dài.

Với iOS, chạy `bundle exec pod install` trong `ios/` trên macOS. AppDelegate đã có callback Google. Cần thêm URL scheme do Google cấp vào `ios/LenVocab/Info.plist` (Xcode → Target → Info → URL Types):

```xml
<key>CFBundleURLTypes</key>
<array>
  <dict>
    <key>CFBundleURLSchemes</key>
    <array><string>YOUR_REVERSED_IOS_CLIENT_ID</string></array>
  </dict>
</array>
```

Giữ các URL Types khác nếu đã có. Không để placeholder trong bản phát hành. Chưa thêm scheme giả vào plist vì hiện chưa có Client ID thật.

## 4. Hợp đồng API

| API | Dữ liệu | Kết quả |
| --- | --- | --- |
| POST `/auth/register` | `username`, `password`, `email?`, `display_name?` | 201 JWT |
| POST `/auth/login` | `username`, `password`; trường `email` cũ vẫn được nhận | 200 JWT |
| POST `/auth/google` | `id_token` | 200 JWT sau xác minh; tạo tài khoản nếu mới |
| POST `/auth/google/link` | Bearer JWT + `id_token` | 200 `{ "message": "GOOGLE_ACCOUNT_LINKED" }` |
| GET `/auth/me` | Bearer JWT | Thêm `username`, `google_linked`; `email` có thể null |

Lỗi: username trùng → 409 `USERNAME_ALREADY_REGISTERED`; email trùng khi đăng ký → 400 `EMAIL_ALREADY_REGISTERED`; sai mật khẩu → 401 `INVALID_CREDENTIALS`; Google token sai → 401 `GOOGLE_TOKEN_INVALID` ở đăng nhập, 400 ở liên kết để không nhầm với phiên app hết hạn; Google bị tắt/mất kết nối chứng chỉ → 503; cần liên kết hoặc đã liên kết tài khoản khác → 409; tài khoản bị khóa → 403 `USER_INACTIVE`.

Không ghi mật khẩu/token/Google claims vào log. Các trường cấu hình nhạy cảm cũng bị loại khỏi repr để tránh lộ trong traceback khi kiểm thử.

## 5. Kiểm tra sau cấu hình

1. Đăng ký bằng username, để trống email → đăng nhập → lưu và ôn thẻ.
2. Kiểm tra username trùng/khác hoa thường, email sai và mật khẩu vượt 72 byte UTF-8.
3. Google → chọn tài khoản mới → nhận phiên → lưu thẻ đang chờ từ chế độ khách.
4. Hủy Google; mất mạng; sai Web Client ID; token sai/hết hạn; tài khoản bị khóa.
5. Email Google trùng tài khoản cũ → đăng nhập bằng mật khẩu → liên kết trong hồ sơ → đăng xuất → đăng nhập Google, vẫn thấy kho từ cũ.
6. Kiểm tra debug/release Android và callback iOS trên thiết bị tương ứng.

Các kiểm thử tự động dùng Google SDK giả và khóa RSA kiểm thử. Chúng không thay thế OAuth thật trên thiết bị hoặc kiểm thử index trên MongoDB thật.

Nguồn triển khai: [Google backend verification](https://developers.google.com/identity/sign-in/android/backend-auth), [SDK Original API](https://react-native-google-signin.github.io/docs/original), [Android setup](https://react-native-google-signin.github.io/docs/setting-up/android), [iOS setup](https://react-native-google-signin.github.io/docs/setting-up/ios).
