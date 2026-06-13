import os
import io
import random
import subprocess
import tempfile
import time
from typing import Optional, Generator, Tuple
from datetime import datetime

from pypdf import PdfWriter, PdfReader, PageObject
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color
from PIL import Image, ImageDraw, ImageFont

from config import Config


class WatermarkService:
    def __init__(self):
        self.watermark_angles = [30, -15, 45, -30, 15, -45]
        self.temp_dir = tempfile.mkdtemp(prefix="sharelink_watermark_")

    def _get_file_type(self, filename: str) -> str:
        ext = os.path.splitext(filename.lower())[1]
        if ext in Config.ALLOWED_PDF_EXTENSIONS:
            return "pdf"
        if ext in Config.ALLOWED_IMAGE_EXTENSIONS:
            return "image"
        if ext in Config.ALLOWED_OFFICE_EXTENSIONS:
            return "office"
        return "other"

    def _generate_watermark_text(self, email: Optional[str], timestamp: float) -> str:
        dt = datetime.fromtimestamp(timestamp)
        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        if email:
            return f"{email} | {time_str}"
        return time_str

    def _convert_office_to_pdf(self, input_path: str) -> Optional[str]:
        try:
            output_dir = tempfile.mkdtemp(dir=self.temp_dir)
            cmd = [
                Config.LIBREOFFICE_PATH,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                output_dir,
                input_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode == 0:
                base_name = os.path.splitext(os.path.basename(input_path))[0]
                pdf_path = os.path.join(output_dir, base_name + ".pdf")
                if os.path.exists(pdf_path):
                    return pdf_path
        except Exception as e:
            print(f"Error converting office to PDF: {e}")
        return None

    def _create_pdf_watermark_overlay(
        self,
        page_width: float,
        page_height: float,
        watermark_text: str,
    ) -> PdfReader:
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(page_width, page_height))

        r, g, b = Config.WATERMARK_COLOR
        gray = r / 255.0
        watermark_color = Color(gray, gray, gray, alpha=Config.WATERMARK_OPACITY)
        c.setFillColor(watermark_color)

        font_size = min(page_width, page_height) / 20
        c.setFont("Helvetica", font_size)

        text_width = c.stringWidth(watermark_text, "Helvetica", font_size)

        positions = []
        margin_x = page_width * 0.1
        margin_y = page_height * 0.1

        cols = 3
        rows = 4
        for row in range(rows):
            for col in range(cols):
                x = margin_x + col * ((page_width - 2 * margin_x) / (cols - 1) if cols > 1 else 0)
                y = margin_y + row * ((page_height - 2 * margin_y) / (rows - 1) if rows > 1 else 0)
                positions.append((x, y))

        for i, (x, y) in enumerate(positions):
            angle = self.watermark_angles[i % len(self.watermark_angles)]
            c.saveState()
            c.translate(x, y)
            c.rotate(angle)
            c.drawCentredString(0, 0, watermark_text)
            c.restoreState()

        for i in range(2):
            x = random.uniform(margin_x, page_width - margin_x)
            y = random.uniform(margin_y, page_height - margin_y)
            angle = random.choice(self.watermark_angles)
            c.saveState()
            c.translate(x, y)
            c.rotate(angle)
            c.drawCentredString(0, 0, watermark_text)
            c.restoreState()

        c.save()
        packet.seek(0)
        return PdfReader(packet)

    def add_pdf_watermark_stream(
        self,
        input_path: str,
        watermark_text: str,
    ) -> Generator[bytes, None, None]:
        reader = PdfReader(input_path)
        writer = PdfWriter()

        for page_num, page in enumerate(reader.pages):
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)

            watermark_reader = self._create_pdf_watermark_overlay(
                width, height, watermark_text
            )
            watermark_page = watermark_reader.pages[0]

            page.merge_page(watermark_page)
            writer.add_page(page)

            chunk = io.BytesIO()
            temp_writer = PdfWriter()
            temp_writer.add_page(page)
            temp_writer.write(chunk)
            yield chunk.getvalue()
            chunk.close()

        final_bytes = io.BytesIO()
        writer.write(final_bytes)
        final_bytes.seek(0)
        yield final_bytes.read()

    def add_pdf_watermark(
        self,
        input_path: str,
        watermark_text: str,
    ) -> bytes:
        reader = PdfReader(input_path)
        writer = PdfWriter()

        for page in reader.pages:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)

            watermark_reader = self._create_pdf_watermark_overlay(
                width, height, watermark_text
            )
            watermark_page = watermark_reader.pages[0]

            page.merge_page(watermark_page)
            writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def add_image_watermark(
        self,
        input_path: str,
        watermark_text: str,
    ) -> bytes:
        with Image.open(input_path) as img:
            img = img.convert("RGBA")
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            width, height = img.size
            font_size = int(min(width, height) / 40)

            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("DejaVuSans.ttf", font_size)
                except:
                    font = ImageFont.load_default()

            r, g, b = Config.WATERMARK_COLOR
            alpha = int(Config.WATERMARK_OPACITY * 255)
            fill_color = (r, g, b, alpha)

            margin = int(min(width, height) * 0.03)

            try:
                bbox = draw.textbbox((0, 0), watermark_text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except:
                text_width = len(watermark_text) * font_size * 0.6
                text_height = font_size

            x = width - text_width - margin
            y = height - text_height - margin

            draw.text((x, y), watermark_text, font=font, fill=fill_color)

            combined = Image.alpha_composite(img, overlay)

            output = io.BytesIO()
            output_format = "PNG"
            if input_path.lower().endswith((".jpg", ".jpeg")):
                combined = combined.convert("RGB")
                output_format = "JPEG"
            combined.save(output, format=output_format, quality=95)
            return output.getvalue()

    def add_watermark(
        self,
        file_path: str,
        filename: str,
        email: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        timestamp = time.time()
        watermark_text = self._generate_watermark_text(email, timestamp)
        file_type = self._get_file_type(filename)

        if file_type == "office":
            pdf_path = self._convert_office_to_pdf(file_path)
            if pdf_path:
                data = self.add_pdf_watermark(pdf_path, watermark_text)
                return data, os.path.splitext(filename)[0] + ".pdf"
            else:
                with open(file_path, "rb") as f:
                    return f.read(), filename

        elif file_type == "pdf":
            data = self.add_pdf_watermark(file_path, watermark_text)
            return data, filename

        elif file_type == "image":
            data = self.add_image_watermark(file_path, watermark_text)
            return data, filename

        else:
            with open(file_path, "rb") as f:
                return f.read(), filename

    def add_watermark_stream(
        self,
        file_path: str,
        filename: str,
        email: Optional[str] = None,
    ) -> Tuple[Generator[bytes, None, None], str]:
        timestamp = time.time()
        watermark_text = self._generate_watermark_text(email, timestamp)
        file_type = self._get_file_type(filename)

        if file_type == "office":
            pdf_path = self._convert_office_to_pdf(file_path)
            if pdf_path:
                stream = self.add_pdf_watermark_stream(pdf_path, watermark_text)
                return stream, os.path.splitext(filename)[0] + ".pdf"
            else:
                def file_stream():
                    with open(file_path, "rb") as f:
                        while True:
                            chunk = f.read(8192)
                            if not chunk:
                                break
                            yield chunk
                return file_stream(), filename

        elif file_type == "pdf":
            stream = self.add_pdf_watermark_stream(file_path, watermark_text)
            return stream, filename

        else:
            data, output_filename = self.add_watermark(file_path, filename, email)
            def data_stream():
                yield data
            return data_stream(), output_filename

    def get_file_path(self, file_id: str) -> str:
        storage_path = os.path.realpath(Config.FILE_STORAGE_PATH)
        safe_file_id = os.path.basename(str(file_id))
        if not safe_file_id or safe_file_id in (".", ".."):
            return os.path.join(storage_path, "__invalid__")

        direct_path = os.path.join(storage_path, safe_file_id)
        real_direct = os.path.realpath(direct_path)
        if os.path.commonpath([storage_path, real_direct]) != storage_path:
            return os.path.join(storage_path, "__invalid__")
        if os.path.exists(real_direct) and os.path.isfile(real_direct):
            return real_direct

        for ext in Config.ALLOWED_PDF_EXTENSIONS | Config.ALLOWED_IMAGE_EXTENSIONS | Config.ALLOWED_OFFICE_EXTENSIONS:
            candidate = os.path.join(storage_path, safe_file_id + ext)
            real_candidate = os.path.realpath(candidate)
            if os.path.commonpath([storage_path, real_candidate]) != storage_path:
                continue
            if os.path.exists(real_candidate):
                return real_candidate

        return direct_path

    def file_exists(self, file_id: str) -> bool:
        file_path = self.get_file_path(file_id)
        storage_path = os.path.realpath(Config.FILE_STORAGE_PATH)
        if os.path.commonpath([storage_path, os.path.realpath(file_path)]) != storage_path:
            return False
        return os.path.exists(file_path) and os.path.isfile(file_path)

    def get_original_filename(self, file_id: str) -> str:
        file_path = self.get_file_path(file_id)
        storage_path = os.path.realpath(Config.FILE_STORAGE_PATH)
        if os.path.commonpath([storage_path, os.path.realpath(file_path)]) == storage_path:
            if os.path.exists(file_path):
                return os.path.basename(file_path)
        safe_file_id = os.path.basename(str(file_id))
        return safe_file_id


watermark_service = WatermarkService()
