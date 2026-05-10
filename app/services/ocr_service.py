# -*- coding: utf-8 -*-
"""
@File    : ocr_service.py
@Desc    : 本地 OCR 服务。优先使用 RapidOCR（如已安装），否则使用 Tesseract。
          两者都未安装时不会阻断上传，只返回 ocr_unavailable。
"""

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Optional
from loguru import logger


@dataclass
class OCRResult:
    text: str
    status: str
    engine: str
    error: Optional[str] = None


class LocalOCRService:
    """本地 OCR 封装，避免把病历照片上传到外部云服务。"""

    def __init__(self):
        self.tesseract_lang = os.getenv("TESSERACT_LANG", "chi_sim+eng")

    def extract_text(self, image_path: str | Path) -> OCRResult:
        path = Path(image_path)
        if not path.exists():
            return OCRResult(text="", status="ocr_failed", engine="none", error="文件不存在")

        # 1) RapidOCR：纯本地 ONNX OCR，适合中文病历；若项目环境未安装则自动跳过。
        rapid = self._try_rapidocr(path)
        if rapid.status == "ocr_success" and rapid.text.strip():
            return rapid

        # 2) Tesseract：需要系统安装 tesseract + 中文语言包 chi_sim。
        tess = self._try_tesseract(path)
        if tess.status == "ocr_success" and tess.text.strip():
            return tess

        # 3) 两个引擎均不可用或未识别出文字。
        errors = "; ".join([e for e in [rapid.error, tess.error] if e])
        if errors:
            logger.warning(f"OCR 未成功: {errors}")
        return OCRResult(
            text="",
            status="ocr_unavailable" if "未安装" in errors or "No module" in errors else "ocr_empty",
            engine="none",
            error=errors or "未识别到文字"
        )

    def _try_rapidocr(self, path: Path) -> OCRResult:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            ocr = RapidOCR()
            result, _ = ocr(str(path))
            lines = []
            if result:
                for item in result:
                    # item 常见结构：[box, text, score]
                    if len(item) >= 2 and item[1]:
                        lines.append(str(item[1]))
            text = "\n".join(lines).strip()
            return OCRResult(text=text, status="ocr_success" if text else "ocr_empty", engine="rapidocr")
        except ModuleNotFoundError as e:
            return OCRResult(text="", status="ocr_unavailable", engine="rapidocr", error=f"RapidOCR 未安装: {e}")
        except Exception as e:
            return OCRResult(text="", status="ocr_failed", engine="rapidocr", error=f"RapidOCR 识别失败: {e}")

    def _try_tesseract(self, path: Path) -> OCRResult:
        try:
            from PIL import Image, ImageOps, ImageEnhance  # type: ignore
            import pytesseract  # type: ignore

            img = Image.open(path)
            img = ImageOps.exif_transpose(img).convert("L")
            img = ImageEnhance.Contrast(img).enhance(1.7)
            text = pytesseract.image_to_string(img, lang=self.tesseract_lang).strip()
            return OCRResult(text=text, status="ocr_success" if text else "ocr_empty", engine="tesseract")
        except ModuleNotFoundError as e:
            return OCRResult(text="", status="ocr_unavailable", engine="tesseract", error=f"pytesseract/Pillow 未安装: {e}")
        except Exception as e:
            return OCRResult(text="", status="ocr_failed", engine="tesseract", error=f"Tesseract 识别失败: {e}")
