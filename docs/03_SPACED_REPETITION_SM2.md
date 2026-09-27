# 03. Thuật Toán Lặp Lại Ngắt Quãng SM-2 (Spaced Repetition)

Tài liệu này mô tả chi tiết nguyên lý, công thức toán học và cách cài đặt thuật toán **SuperMemo-2 (SM-2)** trong dự án LensVocab để tối ưu hóa khả năng ghi nhớ từ vựng cho người học.

---

## 1. Nguyên Lý Khoa Học

Dựa trên **Đường cong quên lãng của Ebbinghaus (Forgetting Curve)**:
* Trí não con người quên đi hơn 50% lượng kiến thức mới tiếp nhận chỉ sau 24 giờ nếu không được ôn tập.
* Thuật toán SM-2 tính toán thời điểm chính xác trước khi từ vựng chuẩn bị trôi khỏi trí nhớ tạm thời để nhắc học viên ôn tập lại. Mỗi lần ôn tập thành công, chu kỳ nhắc lại tiếp theo sẽ được giãn dài ra theo cấp số nhân.

---

## 2. Thang Đo Điểm Chất Lượng Ôn Tập (Quality Score: 0 - 5)

Khi học viên ôn tập một thẻ từ vựng (`POST /api/v1/review/submit`), họ sẽ tự đánh giá mức độ nhớ của mình theo thang điểm:

| Điểm (`quality`) | Định nghĩa | Ý nghĩa thuật toán |
| :---: | :--- | :--- |
| **0** | Quên hoàn toàn (Blackout) | Coi như học lại từ đầu; reset chu kỳ về ngày mai. |
| **1** | Nhớ sai hoàn toàn (Incorrect) | Trả lời sai; reset chu kỳ về ngày mai. |
| **2** | Nhớ sai nhưng thấy đáp án thì nhớ lại | Gần đúng; reset chu kỳ về ngày mai. |
| **3** | Nhớ đúng nhưng rất khó khăn (Hard) | Đạt chuẩn; giữ chu kỳ và giảm hệ số EFactor. |
| **4** | Nhớ đúng sau một chút suy nghĩ (Good) | Đạt chuẩn; tăng chu kỳ theo hệ số EFactor. |
| **5** | Nhớ hoàn hảo ngay lập tức (Perfect) | Đạt chuẩn xuất sắc; tăng chu kỳ và tăng EFactor. |

---

## 3. Công Thức Toán Học Của SM-2

Được triển khai thuần túy tại `app/services/sm2_service.py`:

### 1. Cập nhật hệ số dễ nhớ (Easiness Factor - EFactor)
$$EF' = EF + \Big(0.1 - (5 - q) \times (0.08 + (5 - q) \times 0.02)\Big)$$

* **Giá trị ban đầu:** $EF = 2.5$.
* **Ngưỡng chặn dưới:** $EF' \ge 1.3$ (đảm bảo từ vựng không bao giờ bị kẹt ở hệ số âm hoặc quá nhỏ).

### 2. Cập nhật số lần lặp (`repetitions`) và chu kỳ ôn tập (`interval`)
* **Nếu $quality < 3$ (Học viên nhớ sai hoặc quên):**
  * `repetitions` bị reset về $0$.
  * `interval` bị reset về $1$ ngày (bắt buộc ôn lại vào ngày mai).
* **Nếu $quality \ge 3$ (Học viên nhớ đúng):**
  * `repetitions = repetitions + 1`.
  * Chu kỳ `interval` (số ngày tính từ hôm nay tới lần ôn kế tiếp) được tính như sau:
    $$\text{interval} = \begin{cases}
      1 & \text{khi } \text{repetitions} = 1 \\
      6 & \text{khi } \text{repetitions} = 2 \\
      \text{round}(\text{interval}_{\text{trước}} \times EF') & \text{khi } \text{repetitions} > 2
    \end{cases}$$

### 3. Cập nhật ngày ôn kế tiếp (`next_review_date`)
$$\text{next\_review\_date} = \text{today} + \text{timedelta(days=interval)}$$

---

## 4. Cơ Chế Chống Nản (Anti-demotivation Cap)

Đối với người học mất gốc (A1-A2), việc mở app ra thấy danh sách 50–100 từ dồn ứ sẽ tạo cảm giác choáng ngợp và dẫn đến từ bỏ. Do đó, hệ thống tích hợp **Trần ôn tập linh hoạt** khi lấy hàng đợi (`GET /api/v1/review/today`):

```python
sys_settings = await get_system_settings()
user_goal = current_user.preferences.daily_review_goal or 15

if current_user.account_tier == AccountTier.PREMIUM:
    effective_cap = min(user_goal, sys_settings.premium_daily_review_cap)
else:
    effective_cap = min(user_goal, sys_settings.free_daily_review_cap)

sliced_cards = slice_review_queue(due_cards, cap=effective_cap)
```

* Thẻ đến hạn được sắp xếp theo mức độ ưu tiên: **Từ quá hạn lâu nhất hoặc có ngày ôn cũ nhất sẽ được đưa lên đầu**.
* Hệ thống chỉ cắt đúng số lượng thẻ bằng `effective_cap` (mặc định 15 thẻ cho Free tier) để học viên hoàn thành mục tiêu ngắn gọn mỗi ngày.

---

## 5. Nhật Ký Ôn Tập (`ReviewLog`)

Mỗi lượt nộp kết quả ôn tập sẽ tự động tạo một document trong collection `review_logs`:
* Lưu vết `interval_before`, `interval_after`, `efactor_before`, `efactor_after`, và `quality`.
* Phục vụ cho việc thống kê biểu đồ tiến bộ học tập của người dùng và làm dữ liệu để tinh chỉnh mô hình đề xuất từ vựng sau này.
