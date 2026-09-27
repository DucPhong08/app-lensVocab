from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypeVar

T = TypeVar("T")


@dataclass
class SM2Result:
    interval: int       # số ngày đến lần ôn tiếp theo
    repetitions: int    # streak liên tiếp không quên
    efactor: float      # hệ số dễ nhớ
    next_review_date: date


_MIN_EFACTOR = 1.3
_EFACTOR_FORMULA_CONST = 0.1
_EFACTOR_Q_COEFF_1 = 0.08
_EFACTOR_Q_COEFF_2 = 0.02


def compute_sm2(
    quality: int,
    interval: int,
    repetitions: int,
    efactor: float,
    today: date | None = None,
) -> SM2Result:
    """
    Modified SM-2 algorithm.

    quality: 0-5
      0-2 → Lần ôn tập bị coi là thất bại, reset repetitions về 0
      3-5 → Thành công, tăng dần interval theo efactor

    Công thức efactor gốc SM-2:
      EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))

    today: ngày dùng làm mốc tính next_review_date. Mặc định lấy date.today()
    (giờ local server) nếu không truyền — cho phép caller/test inject ngày cố
    định để hàm deterministic thay vì phụ thuộc đồng hồ hệ thống lúc chạy.
    """
    if quality < 0 or quality > 5:
        raise ValueError(f"quality phải trong khoảng 0-5, nhận được: {quality}")

    if today is None:
        today = date.today()

    # Cập nhật efactor theo công thức SM-2 gốc
    new_efactor = efactor + (_EFACTOR_FORMULA_CONST - (5 - quality) * (_EFACTOR_Q_COEFF_1 + (5 - quality) * _EFACTOR_Q_COEFF_2))
    new_efactor = max(_MIN_EFACTOR, round(new_efactor, 4))

    if quality < 3:
        # Thất bại: reset về đầu
        new_repetitions = 0
        new_interval = 1
    else:
        # Thành công: tính interval mới
        new_repetitions = repetitions + 1
        if new_repetitions == 1:
            new_interval = 1
        elif new_repetitions == 2:
            new_interval = 6
        else:
            new_interval = round(interval * new_efactor)

    next_date = today + timedelta(days=new_interval)

    return SM2Result(
        interval=new_interval,
        repetitions=new_repetitions,
        efactor=new_efactor,
        next_review_date=next_date,
    )


def slice_review_queue(cards: list[T], cap: int = 15) -> list[T]:
    """
    Chống nản: cắt danh sách ôn tập xuống tối đa `cap` từ.
    Các từ còn lại giữ nguyên trạng thái trong DB (next_review_date không thay đổi)
    nên sẽ tự động xuất hiện vào ngày hôm sau khi hàng đợi vơi bớt.

    Precondition: `cards` phải được caller sort theo độ ưu tiên từ trước (vd
    quá hạn nhất trước) — hàm này chỉ cắt theo thứ tự đã truyền vào, không tự sort.
    """
    return cards[:cap]