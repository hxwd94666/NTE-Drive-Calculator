# 共享 OCR 字符字典来源

`ppocr_keys_v1.txt` 原样复制自锁定的 `rapidocr_openvino==1.4.4` 发行包
`rapidocr_openvino/ch_ppocr_rec/ppocr_keys_v1.txt`；该发行包 METADATA 标注 Apache-2.0、
项目 RapidAI/RapidOCR、作者 SWHL。本目录保留完整 Apache-2.0 文本。
字典文件本身没有独立作者或版权声明；这里不推断其个人作者。

- 官方项目：<https://github.com/RapidAI/RapidOCR>
- `uv.lock` 对应 wheel SHA-256：`66cee0f4be5a26b5ae93681073ca88f571ef58c7c20328e8540c052ffc5df07a`
- 字典：26249 字节，SHA-256 `28b2362ad4ab2dc38769aa72feb535e3a9ddb3fd2a7585a05920e6393b1dc7f7`
- 原始字符 6623 项；按两后端 CTC 规则前置 blank、追加空格后为 6625 项。
- 绑定 `ch_PP-OCRv4_rec_infer.onnx` SHA-256：
  `48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b`

当前两个 1.4.4 后端的字符顺序与此模型内嵌 metadata 完全一致；运行时两者均显式
传入这一个字典，并检查后端实际 CTC 的全部顺序。该核对不是原生图片识别性能测试。
模型或后端升级须重新核对原始顺序、blank/空格、输出维数、模型/字典哈希与来源，
更新代码中绑定清单后再验证。构建只排除确切的包内重复字典，不批量排除 txt 文件。
