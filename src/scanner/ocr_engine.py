# 初始化 OCR 后端并识别截图文本。
"""OCR backend selection and text extraction for equipment screenshots."""

import sys
import os
import subprocess
import cv2
import numpy as np
from src.utils.logger import logger
from src.utils.exceptions import OCRParseError

import logging

logging.getLogger("root").setLevel(logging.ERROR)

def _register_runtime_dll_paths() -> None:
    """Register bundled native runtime paths before OCR packages are imported."""
    if sys.platform != "win32":
        return

    base_paths = []
    if getattr(sys, "frozen", False):
        base_paths.append(getattr(sys, "_MEIPASS", ""))
        base_paths.append(os.path.dirname(sys.executable))

    for base in [p for p in base_paths if p]:
        for rel in ("openvino\\libs", "onnxruntime\\capi"):
            dll_dir = os.path.join(base, rel)
            if not os.path.isdir(dll_dir):
                continue
            try:
                os.add_dll_directory(dll_dir)
            except Exception as exc:
                logger.debug(f"DLL 搜索路径注册失败 {dll_dir}: {exc}")
            os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
            if rel == "openvino\\libs":
                os.environ["OPENVINO_LIB_PATHS"] = dll_dir


_register_runtime_dll_paths()


def _get_video_adapter_names() -> list[str]:
    if sys.platform != "win32":
        return []

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    commands = [
        ["wmic", "path", "win32_VideoController", "get", "Name"],
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
        ],
    ]
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=3,
                creationflags=creationflags,
            )
            if result.returncode != 0:
                continue
            names = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and line.strip().lower() != "name"
            ]
            if names:
                return names
        except Exception as exc:
            logger.debug(f"显卡信息读取失败: {exc}")
    return []


def _has_discrete_gpu(adapter_names: list[str]) -> bool:
    discrete_keywords = (
        "nvidia",
        "geforce",
        "rtx",
        "gtx",
        "quadro",
        "radeon",
        "amd",
        "rx ",
        "arc",
    )
    integrated_keywords = (
        "intel(r) uhd",
        "intel uhd",
        "iris",
        "xe graphics",
        "vega",
    )
    for name in adapter_names:
        lower = name.lower()
        if "intel" in lower and "arc" not in lower:
            continue
        if any(k in lower for k in integrated_keywords):
            continue
        if any(k in lower for k in discrete_keywords):
            return True
    return False


def _available_ort_providers() -> list[str]:
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception as exc:
        logger.debug(f"ONNX Runtime provider 查询失败: {exc}")
        return []


def _ocr_backend_preference() -> str:
    """Return user-selected OCR backend preference.

    Default is OpenVINO because it is reliable on Intel iGPU/hybrid laptops.
    DirectML is opt-in: broken dGPUs and non-direct-display laptops can expose
    NVIDIA/AMD adapters but still run DirectML slowly or unstably.
    """
    value = os.environ.get("NTE_OCR_BACKEND", "openvino").strip().lower()
    aliases = {
        "ov": "openvino",
        "intel": "openvino",
        "gpu": "directml",
        "dml": "directml",
        "auto-safe": "auto",
    }
    value = aliases.get(value, value)
    if value not in {"openvino", "directml", "auto", "cpu"}:
        logger.warning(f"未知 OCR 后端配置 NTE_OCR_BACKEND={value!r}，已使用 openvino。")
        return "openvino"
    return value


def _warmup(ocr):
    _test_img = np.zeros((32, 100, 3), dtype=np.uint8)
    ocr(_test_img)


def _create_openvino_ocr():
    try:
        from rapidocr_openvino import RapidOCR
        ocr = RapidOCR(use_cls=False)
        _warmup(ocr)
        return ocr, "OpenVINO (Intel CPU/核显加速)"
    except SystemExit as e:
        logger.warning(f"OpenVINO 触发 sys.exit (DLL路径问题): {e}")
    except Exception as e:
        logger.warning(f"OpenVINO 引擎初始化失败，回退到备选方案: {e}")
    return None, ""


def _create_directml_ocr(providers: list[str]):
    if "DmlExecutionProvider" not in providers:
        logger.warning(f"当前 ONNX Runtime 不包含 DirectML Provider，可用 Provider: {providers}")
        return None, ""
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR(use_cls=False, det_use_dml=True, cls_use_dml=True, rec_use_dml=True)
        _warmup(ocr)
        return ocr, "DirectML GPU (独显加速)"
    except Exception as e:
        logger.warning(f"DirectML OCR 初始化失败，回退到 OpenVINO/CPU: {e}")
        return None, ""


def _create_onnx_cpu_ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR(use_cls=False)
        _warmup(ocr)
        return ocr, "ONNX Runtime CPU (通用模式)"
    except Exception as e:
        logger.warning(f"ONNX Runtime OCR 初始化失败: {e}")
    return None, ""


def _create_ocr_engine():
    """Create OCR engine with safe defaults.

    OpenVINO is the default. DirectML is only used when explicitly requested by
    NTE_OCR_BACKEND=directml/auto and a discrete adapter is detected.
    """
    adapter_names = _get_video_adapter_names()
    has_discrete_gpu = _has_discrete_gpu(adapter_names)
    backend_pref = _ocr_backend_preference()
    if adapter_names:
        logger.info(f"检测到显卡: {'; '.join(adapter_names)}")
    else:
        logger.info("未读取到显卡信息，按无独显策略优先使用 OpenVINO。")

    providers = _available_ort_providers()
    if backend_pref in {"directml", "auto"} and has_discrete_gpu:
        logger.info(f"OCR 后端配置为 {backend_pref}，检测到独立显卡后尝试 DirectML GPU 加速。")
        ocr, engine_type = _create_directml_ocr(providers)
        if ocr is not None:
            return ocr, engine_type
    elif backend_pref in {"directml", "auto"}:
        logger.info("OCR 后端允许 GPU，但未检测到独立显卡，改用 OpenVINO。")
    elif has_discrete_gpu:
        logger.info("检测到独立显卡，但默认安全策略仍优先使用 OpenVINO；如需强制 GPU，可设置 NTE_OCR_BACKEND=directml。")
    else:
        logger.info("未检测到独立显卡，优先使用 OpenVINO 加速。")

    if backend_pref != "cpu":
        ocr, engine_type = _create_openvino_ocr()
        if ocr is not None:
            return ocr, engine_type

    ocr, engine_type = _create_onnx_cpu_ocr()
    if ocr is not None:
        return ocr, engine_type

    raise ImportError("未检测到任何可用的 RapidOCR 推理引擎")


class OCREngine:
    """支持硬件自适应的 OCR 引擎，带运行时兜底"""

    def __init__(self):
        logger.info("正在初始化 OCR 引擎...")
        self.ocr, engine_type = _create_ocr_engine()
        logger.success(f"OCR 引擎就绪 [后端: {engine_type}]")

    def extract_text(self, image_input: np.ndarray) -> list:
        extracted_texts = []
        try:
            if len(image_input.shape) == 2:
                final_input = cv2.cvtColor(image_input, cv2.COLOR_GRAY2BGR)
            else:
                final_input = image_input

            results, _ = self.ocr(final_input)
            if results:
                for line in results:
                    extracted_texts.append(str(line[1]).strip())
        except Exception as e:
            logger.error(f"OCR 切片解析失败: {e}")
            raise OCRParseError(f"OCR 引擎异常: {e}")

        return extracted_texts

    def extract_lines(self, image_input: np.ndarray) -> list[dict]:
        lines = []
        try:
            if len(image_input.shape) == 2:
                final_input = cv2.cvtColor(image_input, cv2.COLOR_GRAY2BGR)
            else:
                final_input = image_input

            results, _ = self.ocr(final_input)
            if not results:
                return lines
            for line in results:
                if len(line) < 2:
                    continue
                box = line[0]
                text = str(line[1]).strip()
                if not text:
                    continue
                try:
                    xs = [float(p[0]) for p in box]
                    ys = [float(p[1]) for p in box]
                    rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
                except Exception:
                    rect = (0, 0, final_input.shape[1], final_input.shape[0])
                lines.append({"text": text, "box": rect})
        except Exception as e:
            logger.error(f"OCR 行框解析失败: {e}")
            raise OCRParseError(f"OCR 引擎异常: {e}")
        return lines

    def identify_item_type(self, identity_image_input: np.ndarray) -> str:
        texts = self.extract_text(identity_image_input)
        full_text = "".join(texts)
        if any(char in full_text for char in ["驱", "动", "型", "I"]):
            return "drive"
        return "tape"
