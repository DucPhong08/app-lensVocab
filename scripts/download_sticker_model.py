"""Download the fixed U2NetP release and verify SHA-256 before installation."""

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
SHA256 = "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"
path = Path(__file__).resolve().parent.parent / "models/u2netp.onnx"
if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256:
    print("Sticker model already verified.")
else:
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(6 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("Sticker model checksum mismatch; nothing installed.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    os.replace(temp_path, path)
    print("Installed verified U2NetP sticker model (4.6 MB).")
