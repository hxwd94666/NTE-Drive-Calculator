# 冻结共享 OCR 字典及模型绑定，核对两个后端实际使用的字符顺序。
from __future__ import annotations

from src.integrations.vision.recognition.ocr.backend_support import OcrBackendSupport

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path

from src.integrations.bundled_resources import bundled_root
from src.integrations.vision.recognition.ocr.model_resources import (
    OcrModelResourceError, OcrModelResources, _file_version, load_ocr_model_resources,
)


@dataclass(frozen=True)
class OcrDictionarySpec:
    filename: str
    size_bytes: int
    sha256: str
    character_count: int
    recognizer_sha256: str


# 字典顺序与已验证的识别模型一一绑定；换模型时必须重新核对输出维数和字符顺序。
OCR_DICTIONARY_SPEC = OcrDictionarySpec(
    'ppocr_keys_v1.txt', 26249,
    '28B2362AD4AB2DC38769AA72FEB535E3A9DDB3FD2A7585A05920E6393B1DC7F7', 6623,
    '48FC40F24F6D2A207A2B1091D3437EB3CC3EB6B676DC3EF9C37384005483683B',
)


def shared_dictionary_path() -> Path:
    return bundled_root() / 'assets' / 'ocr' / OCR_DICTIONARY_SPEC.filename


@dataclass(frozen=True)
class OcrDictionaryFile:
    spec: OcrDictionarySpec
    path: Path
    version: tuple[int, int, int, int, int]
    characters: tuple[str, ...]

    @property
    def decoder_characters(self) -> tuple[str, ...]:
        return ('blank', *self.characters, ' ')

    def verify_unchanged(self) -> None:
        if load_ocr_dictionary(self.path, spec=self.spec, expected_version=self.version) != self:
            raise OcrModelResourceError('OCR 字符字典身份不匹配，请重新解析。')


def load_ocr_dictionary(path: Path, *, spec: OcrDictionarySpec | None = None,
                        expected_version=None) -> OcrDictionaryFile:
    spec = spec if spec is not None else OCR_DICTIONARY_SPEC
    path = Path(path).resolve()
    try:
        before = _file_version(path)
        if before[2] != spec.size_bytes or expected_version is not None and before != expected_version:
            raise OcrModelResourceError('OCR 字符字典大小或文件身份不匹配。')
        payload = path.read_bytes()
        if _file_version(path) != before or hashlib.sha256(payload).hexdigest().upper() != spec.sha256:
            raise OcrModelResourceError('OCR 字符字典 SHA-256 或读取期间身份不匹配。')
        # 精确复现 RapidOCR 1.4.4 的 rb/readlines 和逐行去 CR/LF；不使用会删除
        # 首尾空格的 strip，也不使用会切分其他控制字符的 splitlines。
        characters = tuple(line.decode('utf-8').strip('\n').strip('\r\n')
                           for line in io.BytesIO(payload).readlines())
        if len(characters) != spec.character_count:
            raise OcrModelResourceError('OCR 字符字典数量不匹配。')
        return OcrDictionaryFile(spec, path, before, characters)
    except (OSError, UnicodeError) as error:
        raise OcrModelResourceError('OCR 字符字典读取失败，请恢复共享资源后重试。') from error


@dataclass(frozen=True)
class OcrRuntimeResources:
    models: OcrModelResources
    dictionary: OcrDictionaryFile

    def require_model_binding(self) -> None:
        files = self.models.files
        keys = {entry.spec.keyword for entry in files}
        if len(files) != 3 or keys != {'det_model_path', 'rec_model_path', 'cls_model_path'}:
            raise OcrModelResourceError('OCR 运行资源缺少完整模型集合。')
        rec = next(entry for entry in files if entry.spec.keyword == 'rec_model_path')
        if rec.spec.sha256 != self.dictionary.spec.recognizer_sha256:
            raise OcrModelResourceError('OCR 字符字典与识别模型绑定不匹配。')

    def model_kwargs(self) -> dict[str, str]:
        return {**self.models.model_kwargs(), 'rec_keys_path': str(self.dictionary.path)}

    def verify_unchanged(self) -> None:
        self.require_model_binding()
        self.models.verify_unchanged()
        self.dictionary.verify_unchanged()

    def verify_decoder(self, native) -> None:
        # 后端实际 CTC 以 character 解码；数量相同仍可能错位，必须核对全部顺序、
        # blank 和末尾空格。每图只检查内存小元组，不重哈希整套模型。
        try:
            actual = native.text_rec.postprocess_op.character
        except AttributeError as error:
            raise OcrModelResourceError('OCR 后端缺少受支持的字符解码接口。') from error
        if (not isinstance(actual, (list, tuple)) or not all(isinstance(char, str) for char in actual)
                or tuple(actual) != self.dictionary.decoder_characters):
            raise OcrModelResourceError('OCR 后端字符解码顺序不匹配，请重新初始化。')

    def public_identity(self) -> dict:
        sequence = json.dumps(self.dictionary.decoder_characters, ensure_ascii=False,
                              separators=(',', ':')).encode('utf-8')
        return {'model_sha256': {entry.spec.keyword: entry.spec.sha256 for entry in self.models.files},
                'dictionary_sha256': self.dictionary.spec.sha256,
                'decoder_sha256': hashlib.sha256(sequence).hexdigest()}


def load_ocr_runtime_resources(*, support: OcrBackendSupport | None = None) -> OcrRuntimeResources:
    resources = OcrRuntimeResources(load_ocr_model_resources(support=support), load_ocr_dictionary(shared_dictionary_path()))
    resources.require_model_binding()
    return resources
