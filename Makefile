.PHONY: dev test lint format

# Khởi động server API với hot-reload
dev:
	.venv/bin/uvicorn app.main:app --reload

# Chạy toàn bộ kiểm thử
test:
	.venv/bin/python -m unittest discover tests

# Kiểm tra lỗi cú pháp và code style
lint:
	.venv/bin/ruff check .

# Tự động format code
format:
	.venv/bin/ruff format .
