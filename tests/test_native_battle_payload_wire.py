# 验证原生证据的无损传输、未知值和展开上限。
import json
import unittest
from unittest.mock import patch

from src.integrations.battle_analysis.payload_wire import restore_payloads
from src.integrations.nte_analysis_core import NativeAnalysisError


class NativePayloadWireTests(unittest.TestCase):
    def test_restore_preserves_text_nulls_and_input(self):
        raw = '{ "文字": "逗号,转义\\n", "v":null, "v":2 }'
        parts = [raw[:12], raw[12:]]
        hit = {"native_evidence": {"payload_fragments": [0, 1], "static_names": []}}
        page = {"native_payload_fragments": parts,
                "analysis": {"hits": [hit], "timeline_hits": [{"native_evidence": None}]},
                "candidate_display_analysis": None}
        before = json.dumps(page)
        restored = restore_payloads(page)
        self.assertEqual(restored["analysis"]["hits"][0]["native_evidence"]["payload_json"], raw)
        self.assertIsNone(restored["analysis"]["timeline_hits"][0]["native_evidence"])
        self.assertIsNone(restored["candidate_display_analysis"])
        self.assertEqual(json.dumps(page), before)

    def test_invalid_references_and_conflicting_encodings_fail(self):
        for indices in ([True], [-1], [1], [0.0], "0"):
            page = {"native_payload_fragments": ["{}"], "analysis": {
                "hits": [{"native_evidence": {"payload_fragments": indices}}]}}
            with self.subTest(indices=indices), self.assertRaises(NativeAnalysisError):
                restore_payloads(page)
        for other in ("payload_json", "reference_event_id"):
            page = {"native_payload_fragments": ["{}"], "analysis": {
                "hits": [{"native_evidence": {"payload_fragments": [0], other: "{}"}}]}}
            with self.subTest(other=other), self.assertRaises(NativeAnalysisError):
                restore_payloads(page)

    def test_expansion_budget_counts_utf8_and_both_snapshots(self):
        hit = {"native_evidence": {"payload_fragments": [0]}}
        page = {"native_payload_fragments": ["中文"], "analysis": {"hits": [hit]},
                "candidate_display_analysis": {"hits": [hit]}}
        with patch("src.integrations.battle_analysis.payload_wire.MAX_EXPANDED_PAYLOAD_BYTES", 11):
            with self.assertRaisesRegex(NativeAnalysisError, "大小限制"):
                restore_payloads(page)
        with patch("src.integrations.battle_analysis.payload_wire.MAX_EXPANDED_PAYLOAD_BYTES", 12):
            self.assertEqual(restore_payloads(page)["analysis"]["hits"][0]["native_evidence"]["payload_json"], "中文")
