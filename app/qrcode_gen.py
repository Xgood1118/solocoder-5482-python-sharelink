import io
from typing import Optional

import qrcode
from qrcode.image.pil import PilImage


class QRCodeGenerator:
    def generate_qrcode(self, url: str, size: int = 300) -> bytes:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white", image_factory=PilImage)
        img = img.resize((size, size))

        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

    def generate_qrcode_stream(self, url: str, size: int = 300):
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white", image_factory=PilImage)
        img = img.resize((size, size))

        output = io.BytesIO()
        img.save(output, format="PNG")
        output.seek(0)
        return output


qrcode_generator = QRCodeGenerator()
