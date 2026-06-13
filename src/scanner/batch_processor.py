"""Offline screenshot parser that turns scanned images into inventory items."""

import os
import json
import cv2
import time
import shutil
import re

from src.scanner.config import ScannerConfig
from src.scanner.shape_recognizer import ShapeRecognizer
from src.scanner.ocr_engine import OCREngine
from src.scanner.parser import DriveDataParser
from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode

# 引入基座定义的全局日志与异常类
from src.utils.logger import logger
from src.utils.exceptions import InventoryEmptyError


class BatchProcessor:
    """全自动离线批处理管线，支持增量归档"""
    DRIVE_TYPE_CONFIDENCE = 0.86

    def __init__(self, input_dir: str = "scanned_images", output_file: str = "config/real_inventory.json", config_dir: str = "config", replace_output: bool = False):
        self.input_dir = input_dir
        self.output_file = output_file
        self.replace_output = replace_output

        # 归档文件夹（已解析的图片移至此处）
        self.archive_dir = os.path.join(self.input_dir, "archive")
        os.makedirs(self.archive_dir, exist_ok=True)

        logger.info("=" * 60)
        logger.info("离线批处理管线启动")
        logger.info("=" * 60)

        self.shape_recognizer = ShapeRecognizer(template_dir=os.path.join(config_dir, "templates"))
        self.ocr_engine = OCREngine()
        self.parser = DriveDataParser(config_dir=config_dir)
        self.inventory = []
        self.successful_image_paths = []
        self._last_parsed_filename = None
        self._last_parsed_signature = None

    def process_all(self):
        if not os.path.exists(self.input_dir):
            raise InventoryEmptyError(f"找不到截图文件夹 {self.input_dir}，请先执行扫描！")

        # 只读取根目录的图片，跳过 archive 文件夹
        image_files = [f for f in os.listdir(self.input_dir) if
                       f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")) and os.path.isfile(os.path.join(self.input_dir, f))]
        image_files.sort()
        total_files = len(image_files)

        if total_files == 0:
            logger.info("收件箱为空，没有新的截图需要处理。")
            return

        logger.info(f"\n发现 {total_files} 张未处理截图，开始解析...\n")
        start_time = time.time()
        success_count = 0

        for idx, filename in enumerate(image_files, 1):
            file_path = os.path.join(self.input_dir, filename)
            try:
                t1 = time.time()
                if self._skip_duplicate_image(file_path):
                    logger.info(f"[{idx:04d}/{total_files:04d}] 跳过重复截图: {filename}")
                    continue
                item_obj = self._process_single_image(file_path)
                cost = time.time() - t1

                logger.info(f"[{idx:04d}/{total_files:04d}] 解析: {cost:.2f}s | {filename}")

                if item_obj.item_type == 'drive':
                    logger.info(f"      > [驱动] | 形状: {item_obj.shape_id.ljust(8)} | 品质: {item_obj.quality}")
                else:
                    logger.info(
                        f"      > [卡带] | 套装: {getattr(item_obj, 'set_name', '未知').ljust(8)} | 品质: {item_obj.quality}")

                logger.info(f"      > 主词条: {item_obj.main_stats}")
                logger.info(f"      > 副词条: {item_obj.sub_stats}\n")

                # 解析成功后将原图移动到归档区
                self._mark_image_success(file_path)
                success_count += 1

            except Exception as e:
                logger.error(f"[{idx:04d}/{total_files:04d}] 解析失败: {filename} | 错误: {str(e)}\n")
                # 发生错误的图片留在原处，方便玩家后续排查问题

        # 仅当有成功解析的数据时才落盘
        if success_count > 0:
            self._export_to_json()

        cost_time = time.time() - start_time
        logger.success("=" * 60)
        logger.success(f"解析完成。本次处理 {success_count} 个装备，已入库。")
        logger.success(f"总耗时: {cost_time:.2f} 秒 (平均 {cost_time / total_files:.2f} 秒/个)")
        logger.success("=" * 60)

    def process_all(self):
        if not os.path.exists(self.input_dir):
            raise InventoryEmptyError(f"找不到截图文件夹 {self.input_dir}，请先执行扫描！")

        image_files = [
            f for f in os.listdir(self.input_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
            and os.path.isfile(os.path.join(self.input_dir, f))
        ]
        image_files.sort()
        total_files = len(image_files)

        if total_files == 0:
            logger.info("收件箱为空，没有新的截图需要处理。")
            return

        logger.info(f"\n发现 {total_files} 张未处理截图，开始解析...\n")
        start_time = time.time()
        success_count = 0
        duplicate_count = 0

        for idx, filename in enumerate(image_files, 1):
            file_path = os.path.join(self.input_dir, filename)
            try:
                t1 = time.time()
                item_obj, added = self.process_image_file(file_path, filename)
                cost = time.time() - t1

                logger.info(f"[{idx:04d}/{total_files:04d}] 解析: {cost:.2f}s | {filename}")
                if not added:
                    duplicate_count += 1
                    logger.info("      > 命名相邻截图解析数据一致，已过滤重复入库\n")
                    continue

                if item_obj.item_type == "drive":
                    logger.info(f"      > [驱动] | 形状: {item_obj.shape_id.ljust(8)} | 品质: {item_obj.quality}")
                else:
                    logger.info(
                        f"      > [卡带] | 套装: {getattr(item_obj, 'set_name', '未知').ljust(8)} | 品质: {item_obj.quality}"
                    )

                logger.info(f"      > 主词条: {item_obj.main_stats}")
                logger.info(f"      > 副词条: {item_obj.sub_stats}\n")
                success_count += 1

            except Exception as e:
                logger.error(f"[{idx:04d}/{total_files:04d}] 解析失败: {filename} | 错误: {str(e)}\n")

        if success_count > 0:
            self._export_to_json()

        cost_time = time.time() - start_time
        avg_time = cost_time / total_files if total_files else 0
        logger.success("=" * 60)
        logger.success(f"解析完成。本次入库 {success_count} 个装备，过滤相邻重复 {duplicate_count} 个。")
        logger.success(f"总耗时: {cost_time:.2f} 秒 (平均 {avg_time:.2f} 秒/张)")
        logger.success("=" * 60)

    def archive_processed_images(self, image_paths=None) -> int:
        """Move successfully parsed screenshots into archive after allocation is saved."""
        paths = list(image_paths if image_paths is not None else self.successful_image_paths)
        archived_count = 0
        for file_path in paths:
            if not os.path.exists(file_path):
                continue
            filename = os.path.basename(file_path)
            archive_path = os.path.join(self.archive_dir, filename)
            base, ext = os.path.splitext(archive_path)
            suffix = 1
            while os.path.exists(archive_path):
                archive_path = f"{base}_{suffix}{ext}"
                suffix += 1
            shutil.move(file_path, archive_path)
            archived_count += 1
        if archived_count:
            logger.success(f"已归档 {archived_count} 张已保存配装的截图。")
        return archived_count

    def _image_hash(self, image_path: str) -> str:
        digest = hashlib.sha1()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _skip_duplicate_image(self, image_path: str) -> bool:
        digest = self._image_hash(image_path)
        if digest not in self._successful_image_hashes:
            return False
        self.successful_image_paths.append(image_path)
        return True

    def _mark_image_success(self, image_path: str) -> None:
        self.successful_image_paths.append(image_path)

    def process_image_file(self, image_path: str, filename: str | None = None):
        item_data = self._process_single_image(image_path)
        current_name = filename or os.path.basename(image_path)
        current_signature = self._item_signature(item_data)
        is_adjacent_duplicate = (
            self._last_parsed_signature == current_signature
            and self._are_named_neighbors(self._last_parsed_filename, current_name)
        )

        self._last_parsed_filename = current_name
        self._last_parsed_signature = current_signature
        self._mark_image_success(image_path)

        if is_adjacent_duplicate:
            return item_data, False

        self.inventory.append(item_data)
        return item_data, True

    def parse_identify_items(
        self,
        image_path: str,
        max_items: int = 12,
        forced_type: str | None = None,
        forced_shape_id: str | None = None,
        forced_set_name: str | None = None,
        forced_main_stat: str | None = None,
    ):
        img = imread_unicode(image_path)
        if img is None:
            raise ValueError("图像损坏或无法读取")
        img = crop_window_border_from_image(img)

        items = []
        if forced_type is not None:
            try:
                item = self._process_identify_standard_forced(
                    img,
                    forced_type=forced_type,
                    forced_shape_id=forced_shape_id,
                    forced_set_name=forced_set_name,
                    forced_main_stat=forced_main_stat,
                )
                if self._is_valid_identify_item(item):
                    items.append(item)
                    return items
            except Exception as exc:
                logger.debug(f"标准区域强制鉴定解析未命中，切换整图识别: {exc}")
        else:
            try:
                item = self._process_single_image(image_path)
                if item and item.sub_stats:
                    items.append(item)
            except Exception as exc:
                logger.debug(f"标准区域鉴定解析未命中，切换整图识别: {exc}")

        try:
            lines = self.ocr_engine.extract_lines(img)
            for cluster in self._cluster_identify_lines(lines, img.shape[:2]):
                item = self._synthesize_identify_cluster(
                    img,
                    cluster,
                    forced_type=forced_type,
                    forced_shape_id=forced_shape_id,
                    forced_set_name=forced_set_name,
                    forced_main_stat=forced_main_stat,
                )
                if self._is_valid_identify_item(item):
                    items.append(item)
                    if len(items) >= max_items:
                        break
        except Exception as exc:
            logger.debug(f"整图鉴定解析失败: {exc}")

        return items if forced_type is not None else self._dedupe_identify_items(items)

    def _is_valid_identify_item(self, item) -> bool:
        if item is None or len(getattr(item, "sub_stats", {}) or {}) < 2:
            return False
        bad_keywords = ("附近", "最多", "持续", "每层", "每有", "受到", "造成", "装备者", "角色位于")
        for name in item.sub_stats.keys():
            if any(keyword in str(name) for keyword in bad_keywords):
                return False
        return True

    def _process_identify_standard_forced(
        self,
        img,
        forced_type: str,
        forced_shape_id: str | None = None,
        forced_set_name: str | None = None,
        forced_main_stat: str | None = None,
    ):
        height, width = img.shape[:2]
        region_profiles = ScannerConfig.get_region_profiles(target_width=width, target_height=height)
        _, regions = region_profiles[0]
        if forced_type == "drive":
            if not forced_shape_id:
                raise ValueError("未选择驱动形状")
            sub_box = regions["drive_sub_stats"]
            sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]
            raw_sub_texts = self.ocr_engine.extract_text(sub_crop)
            return self.parser.synthesize_drive(forced_shape_id, raw_sub_texts)

        if forced_type == "tape":
            if not forced_set_name:
                raise ValueError("未选择卡带套装")
            main_box = regions["tape_main_stat"]
            sub_box = regions["tape_sub_stats"]
            main_crop = img[main_box[1]:main_box[3], main_box[0]:main_box[2]]
            sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]
            raw_main_texts = self.ocr_engine.extract_text(main_crop)
            raw_sub_texts = self.ocr_engine.extract_text(sub_crop)
            if not raw_main_texts:
                raw_main_texts = [""]
            main_texts = [forced_main_stat] if forced_main_stat else raw_main_texts
            item = self.parser.synthesize_tape(forced_set_name, main_texts, raw_sub_texts)
            if forced_main_stat:
                item.main_stats = forced_main_stat
            return item

        raise ValueError(f"未知鉴定类型: {forced_type}")

    def _item_signature(self, item_data) -> str:
        data = item_data.model_dump()
        data.pop("uid", None)
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _cluster_identify_lines(self, lines: list[dict], image_shape: tuple[int, int]) -> list[list[dict]]:
        height, width = image_shape
        stat_lines = [line for line in lines if self._is_identify_stat_candidate(line.get("text", ""))]
        if not stat_lines:
            return []

        parent = list(range(len(stat_lines)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        x_limit = max(150, int(width * 0.11))
        y_limit = max(180, int(height * 0.24))
        for i, a in enumerate(stat_lines):
            ax1, ay1, ax2, ay2 = a["box"]
            acx, acy = (ax1 + ax2) / 2, (ay1 + ay2) / 2
            for j in range(i + 1, len(stat_lines)):
                b = stat_lines[j]
                bx1, by1, bx2, by2 = b["box"]
                bcx, bcy = (bx1 + bx2) / 2, (by1 + by2) / 2
                if abs(acx - bcx) <= x_limit and abs(acy - bcy) <= y_limit:
                    union(i, j)

        grouped = {}
        for idx, line in enumerate(stat_lines):
            grouped.setdefault(find(idx), []).append(line)

        clusters = []
        for group in grouped.values():
            x1 = min(line["box"][0] for line in group)
            y1 = min(line["box"][1] for line in group)
            x2 = max(line["box"][2] for line in group)
            y2 = max(line["box"][3] for line in group)
            clusters.append(group)
        clusters.sort(key=lambda group: (min(line["box"][1] for line in group), min(line["box"][0] for line in group)))
        return clusters

    def _box_intersects(self, box, region) -> bool:
        x1, y1, x2, y2 = box
        rx1, ry1, rx2, ry2 = region
        return not (x2 < rx1 or x1 > rx2 or y2 < ry1 or y1 > ry2)

    def _synthesize_identify_cluster(
        self,
        img,
        lines: list[dict],
        forced_type: str | None = None,
        forced_shape_id: str | None = None,
        forced_set_name: str | None = None,
        forced_main_stat: str | None = None,
    ):
        texts = [line.get("text", "") for line in lines if line.get("text")]
        stat_texts = self._identify_stat_texts(lines, forced_type=forced_type)
        parse_texts = stat_texts if stat_texts else ([] if forced_type else texts)
        if not self.parser._clean_stats(parse_texts):
            return None

        if forced_type == "tape":
            set_name = forced_set_name or self.parser._fuzzy_match_set_name("".join(texts))
            main_texts = [forced_main_stat] if forced_main_stat else ["".join(texts)]
            item = self.parser.synthesize_tape(set_name, main_texts, parse_texts[-4:])
            if forced_main_stat:
                item.main_stats = forced_main_stat
            return item
        if forced_type == "drive":
            if not forced_shape_id:
                return None
            return self.parser.synthesize_drive(forced_shape_id, parse_texts[:4])

        joined = "".join(texts)
        set_name = self.parser._fuzzy_match_set_name(joined)
        main_stat = self.parser._fuzzy_match_tape_main(joined)
        looks_tape = self._looks_like_tape_identity(joined) or (
            set_name in self.parser.REAL_SETS_WHITE_LIST and main_stat in self.parser.TAPE_MAIN_STATS_POOL
        )
        if looks_tape and not self._looks_like_drive_identity(joined):
            return self.parser.synthesize_tape(set_name, [joined], parse_texts[-4:])

        x1 = min(line["box"][0] for line in lines)
        y1 = min(line["box"][1] for line in lines)
        x2 = max(line["box"][2] for line in lines)
        y2 = max(line["box"][3] for line in lines)
        height, width = img.shape[:2]
        search_region = (
            max(0, x1 - int(width * 0.35)),
            max(0, y1 - int(height * 0.45)),
            min(width, x2 + int(width * 0.08)),
            min(height, y2 + int(height * 0.10)),
        )
        shape = self._locate_shape_in_image(img, search_region)
        if shape["shape_id"] == "Unknown":
            shape = self._locate_shape_in_image(img, None)
        if shape["shape_id"] == "Unknown":
            logger.debug(f"鉴定候选未找到形状，跳过: {texts}")
            return None
        return self.parser.synthesize_drive(shape["shape_id"], parse_texts[:4])

    def _identify_stat_texts(self, lines: list[dict], forced_type: str | None = None) -> list[str]:
        bad_keywords = (
            "史诗", "传说", "附近", "最多", "持续", "每层", "每有", "受到", "造成",
            "装备者", "角色位于", "依旧生效", "推荐", "装配一个", "每装配",
        )
        good_keywords = ("增加", "提升", "增强", "%")
        texts = []
        for line in sorted(lines, key=lambda item: (item["box"][1], item["box"][0])):
            text = (line.get("text") or "").strip()
            if not any(ch.isdigit() for ch in text):
                continue
            if any(keyword in text for keyword in bad_keywords):
                continue
            if forced_type in ("drive", "tape") and not any(keyword in text for keyword in good_keywords):
                continue
            if len(text) > 24:
                continue
            texts.append(text)
        return texts

    def _is_identify_stat_candidate(self, text: str) -> bool:
        text = (text or "").strip()
        if not any(ch.isdigit() for ch in text):
            return False
        bad_keywords = (
            "史诗", "传说", "附近", "最多", "持续", "每层", "每有", "受到", "造成",
            "装备者", "角色位于", "依旧生效", "推荐", "装配一个", "每装配", "UID",
        )
        if any(keyword in text for keyword in bad_keywords):
            return False
        return any(keyword in text for keyword in ("增加", "提升", "增强", "%"))

    def _locate_shape_in_image(self, img, region=None) -> dict:
        if region is not None:
            x1, y1, x2, y2 = region
            search = img[y1:y2, x1:x2]
        else:
            search = img
        if search is None or search.size == 0:
            return {"shape_id": "Unknown", "confidence": -1.0}
        gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY) if len(search.shape) == 3 else search
        search_h, search_w = gray.shape[:2]
        best_shape = "Unknown"
        best_score = -1.0
        base_scale = max(0.35, min(1.6, search_w / 900))
        scales = sorted({0.35, 0.45, 0.55, 0.65, 0.75, 0.9, 1.0, 1.15, 1.3, round(base_scale, 2)})
        for shape_id, template in self.shape_recognizer.templates.items():
            th, tw = template.shape[:2]
            for scale in scales:
                rw, rh = int(tw * scale), int(th * scale)
                if rw < 32 or rh < 32 or rw > search_w or rh > search_h:
                    continue
                resized = cv2.resize(template, (rw, rh))
                res = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                if max_val > best_score:
                    best_score = max_val
                    best_shape = shape_id
        if best_score < 0.58:
            best_shape = "Unknown"
        return {"shape_id": best_shape, "confidence": round(float(best_score), 2)}

    def _dedupe_identify_items(self, items: list) -> list:
        deduped = []
        seen = set()
        for item in items:
            signature = self._item_signature(item)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(item)
        return deduped

    def _filename_sequence_key(self, filename: str | None):
        if not filename:
            return None
        stem = os.path.splitext(os.path.basename(filename))[0]
        match = re.match(r"^(.*?)(\d+)$", stem)
        if not match:
            return None
        return match.group(1), int(match.group(2))

    def _are_named_neighbors(self, previous_filename: str | None, current_filename: str | None) -> bool:
        previous_key = self._filename_sequence_key(previous_filename)
        current_key = self._filename_sequence_key(current_filename)
        if not previous_key or not current_key:
            return False
        return previous_key[0] == current_key[0] and current_key[1] == previous_key[1] + 1

    def _process_single_image(self, image_path: str):
        img = imread_unicode(image_path)
        if img is None:
            raise ValueError("图像损坏或无法读取")
        img = crop_window_border_from_image(img)

        height, width = img.shape[:2]
        region_profiles = ScannerConfig.get_region_profiles(target_width=width, target_height=height)
        item_type, profile_name, regions, shape_res, hub_joined_text = self._classify_item(img, region_profiles)
        logger.debug(
            f"截图坐标方案: {profile_name} | 尺寸: {width}x{height} | "
            f"类型: {item_type} | 形状: {shape_res['shape_id']}({shape_res['confidence']}) | "
            f"身份文本: {hub_joined_text}"
        )

        if item_type == "drive":
            if shape_res["shape_id"] == "Unknown" or shape_res["confidence"] < 0.7:
                raise ValueError(f"形状识别置信度不足: {shape_res['confidence']}")

            sub_box = regions["drive_sub_stats"]
            sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]
            raw_sub_texts = self.ocr_engine.extract_text(sub_crop)

            item_data = self.parser.synthesize_drive(shape_res["shape_id"], raw_sub_texts)
        else:
            set_name = self.parser._fuzzy_match_set_name(hub_joined_text)

            main_box = regions["tape_main_stat"]
            sub_box = regions["tape_sub_stats"]

            main_crop = img[main_box[1]:main_box[3], main_box[0]:main_box[2]]
            sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]

            raw_main_texts = self.ocr_engine.extract_text(main_crop)
            raw_sub_texts = self.ocr_engine.extract_text(sub_crop)

            item_data = self.parser.synthesize_tape(set_name, raw_main_texts, raw_sub_texts)

        return item_data

    def _classify_item(self, img, region_profiles):
        candidates = []

        for profile_name, regions in region_profiles:
            shape_box = regions["drive_shape_icon"]
            crop = img[shape_box[1]:shape_box[3], shape_box[0]:shape_box[2]]
            if crop.size == 0:
                continue
            shape_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            shape_res = self.shape_recognizer.recognize(shape_crop)

            id_box = regions["identity_check"]
            id_crop = img[id_box[1]:id_box[3], id_box[0]:id_box[2]]
            raw_hub_texts = self.ocr_engine.extract_text(id_crop)
            hub_text = "".join(raw_hub_texts)

            candidates.append({
                "profile": profile_name,
                "regions": regions,
                "shape": shape_res,
                "hub_text": hub_text,
                "is_drive_text": self._looks_like_drive_identity(hub_text),
                "is_tape_text": self._looks_like_tape_identity(hub_text),
            })
            logger.debug(
                f"候选坐标[{profile_name}] identity={id_box} shape={shape_box} "
                f"text={hub_text} shape={shape_res['shape_id']}({shape_res['confidence']})"
            )

        if not candidates:
            profile_name, regions = region_profiles[0]
            return "tape", profile_name, regions, {"shape_id": "Unknown", "confidence": -1.0}, ""

        # Card/tape identity wins over shape false positives. Tape pages can
        # contain icon art in the drive-shape area, so OpenCV alone is unsafe.
        tape_candidates = [c for c in candidates if c["is_tape_text"] and not c["is_drive_text"]]
        if tape_candidates:
            chosen = max(tape_candidates, key=lambda c: len(c["hub_text"]))
            return "tape", chosen["profile"], chosen["regions"], chosen["shape"], chosen["hub_text"]

        drive_text_candidates = [c for c in candidates if c["is_drive_text"]]
        if drive_text_candidates:
            chosen = max(drive_text_candidates, key=lambda c: c["shape"]["confidence"])
            return "drive", chosen["profile"], chosen["regions"], chosen["shape"], chosen["hub_text"]

        best_shape_candidate = max(candidates, key=lambda c: c["shape"]["confidence"])
        if (
            best_shape_candidate["shape"]["shape_id"] != "Unknown"
            and best_shape_candidate["shape"]["confidence"] >= self.DRIVE_TYPE_CONFIDENCE
        ):
            return (
                "drive",
                best_shape_candidate["profile"],
                best_shape_candidate["regions"],
                best_shape_candidate["shape"],
                best_shape_candidate["hub_text"],
            )

        # If neither identity OCR nor high-confidence shape says drive, treat it
        # as tape so a decorative false match cannot steal all cartridges.
        chosen = max(candidates, key=lambda c: len(c["hub_text"]))
        return "tape", chosen["profile"], chosen["regions"], chosen["shape"], chosen["hub_text"]

    def _looks_like_drive_identity(self, text: str) -> bool:
        clean = (text or "").replace(" ", "")
        if "卡带" in clean:
            return False
        if "驱动型" in clean or "驱动" in clean:
            return True
        return "驱" in clean and "动" in clean

    def _looks_like_tape_identity(self, text: str) -> bool:
        clean = (text or "").replace(" ", "")
        if not clean:
            return False
        if "卡带" in clean:
            return True

        only_cn = "".join(ch for ch in clean if "\u4e00" <= ch <= "\u9fff")
        for set_name in self.parser.REAL_SETS_WHITE_LIST:
            set_cn = "".join(ch for ch in set_name if "\u4e00" <= ch <= "\u9fff")
            if set_cn and (set_cn in only_cn or only_cn in set_cn and len(only_cn) >= 3):
                return True
        return False

    def _export_to_json(self):
        """增量合并：与现有数据去重后写入"""
        existing_inventory = []
        existing_uids = set()

        # 1. 尝试读取现有的老仓库
        if os.path.exists(self.output_file):
            try:
                with open(self.output_file, 'r', encoding='utf-8') as f:
                    existing_inventory = json.load(f)
                    existing_uids = {item['uid'] for item in existing_inventory}
            except Exception:
                pass

        # 2. 增量查重合并
        new_count = 0
        for item in self.inventory:
            data = item.model_dump()
            if data['uid'] not in existing_uids:
                existing_inventory.append(data)
                existing_uids.add(data['uid'])
                new_count += 1

        # 3. 落盘保存
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(existing_inventory, f, ensure_ascii=False, indent=4)

        logger.success(
            f"仓库增量更新完成。新入库 {new_count} 个，总库存: {len(existing_inventory)} 个。")
    def _export_to_json(self):
        """Merge parsed items without UID-based de-duplication."""
        existing_inventory = []
        existing_uids = set()

        if not self.replace_output and os.path.exists(self.output_file):
            try:
                with open(self.output_file, "r", encoding="utf-8") as f:
                    existing_inventory = json.load(f)
                existing_uids = {
                    item.get("uid")
                    for item in existing_inventory
                    if isinstance(item, dict) and item.get("uid")
                }
            except Exception:
                existing_inventory = []
                existing_uids = set()

        new_count = 0
        for item in self.inventory:
            data = item.model_dump()
            uid = data.get("uid") or f"item_{int(time.time() * 1000)}"
            data["uid"] = self._make_unique_uid(uid, existing_uids)
            existing_inventory.append(data)
            existing_uids.add(data["uid"])
            new_count += 1

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(existing_inventory, f, ensure_ascii=False, indent=4)

        logger.success(f"仓库增量更新完成。新入库 {new_count} 个，总库存 {len(existing_inventory)} 个。")

    def _make_unique_uid(self, uid: str, existing_uids: set) -> str:
        if uid not in existing_uids:
            return uid
        base = uid
        suffix = 2
        while f"{base}_{suffix}" in existing_uids:
            suffix += 1
        return f"{base}_{suffix}"
