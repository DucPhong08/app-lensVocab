.PHONY: dev test lint format install

# Tạo môi trường ảo và cài toàn bộ thư viện từ requirements.txt
install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

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
