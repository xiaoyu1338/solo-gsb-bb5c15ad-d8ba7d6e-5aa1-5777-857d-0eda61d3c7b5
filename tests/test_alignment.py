"""算法核心单元测试（标准库 unittest，无需额外依赖）。"""

import math
import unittest

from app.alignment import AlignmentError, AlignmentParams, align_beats


class TestAlignBeats(unittest.TestCase):
    def test_perfect_match(self):
        ref = [0.0, 0.5, 1.0, 1.5, 2.0]
        perf = [0.0, 0.5, 1.0, 1.5, 2.0]
        out = align_beats(ref, perf)
        self.assertEqual(out["statistics"]["matched_count"], 5)
        self.assertEqual(out["statistics"]["missed_count"], 0)
        self.assertEqual(out["statistics"]["inserted_count"], 0)
        self.assertAlmostEqual(out["statistics"]["total_cost"], 0.0)
        pairs = [(b["ref_index"], b["perf_index"]) for b in out["matched_beats"]]
        self.assertEqual(pairs, [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)])

    def test_drift_early_and_late(self):
        # 前两拍抢拍（负偏差），后两拍拖拍（正偏差）
        ref = [0.0, 0.5, 1.0, 1.5]
        perf = [-0.05, 0.44, 1.06, 1.57]
        out = align_beats(ref, perf, AlignmentParams(on_time_threshold=0.03))
        statuses = [b["status"] for b in out["matched_beats"]]
        self.assertEqual(statuses, ["early", "early", "late", "late"])
        # 同向连续区间：early 一段、late 一段
        kinds = [iv["status"] for iv in out["signed_intervals"]]
        self.assertEqual(kinds, ["early", "late"])
        self.assertEqual(out["signed_intervals"][0]["beat_count"], 2)
        self.assertEqual(out["signed_intervals"][1]["beat_count"], 2)

    def test_missed_beat(self):
        ref = [0.0, 0.5, 1.0, 1.5, 2.0]
        perf = [0.0, 0.5, 1.5, 2.0]  # 第 3 拍（索引2）漏拍
        out = align_beats(ref, perf, AlignmentParams(band_width=2))
        self.assertEqual(out["statistics"]["missed_count"], 1)
        self.assertEqual([b["ref_index"] for b in out["missed_beats"]], [2])
        self.assertEqual(out["statistics"]["inserted_count"], 0)
        pairs = [(b["ref_index"], b["perf_index"]) for b in out["matched_beats"]]
        self.assertEqual(pairs, [(0, 0), (1, 1), (3, 2), (4, 3)])
        # 缺口索引 2 把偏差段切成两段
        self.assertEqual(len(out["deviation_segments"]), 2)

    def test_inserted_beat(self):
        ref = [0.0, 0.5, 1.0, 1.5]
        perf = [0.0, 0.5, 0.75, 1.0, 1.5]  # 0.75 是插入音
        out = align_beats(ref, perf, AlignmentParams(band_width=2))
        self.assertEqual(out["statistics"]["inserted_count"], 1)
        self.assertEqual([b["perf_index"] for b in out["inserted_beats"]], [2])
        self.assertEqual(out["statistics"]["missed_count"], 0)
        pairs = [(b["ref_index"], b["perf_index"]) for b in out["matched_beats"]]
        self.assertEqual(pairs, [(0, 0), (1, 1), (2, 3), (3, 4)])

    def test_combined_miss_insert_drift(self):
        ref = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
        # 漏 1.0（索引2），在 1.73 插入一个音，其余略有抢/拖
        perf = [0.0, 0.48, 1.52, 1.73, 2.02, 2.5]
        out = align_beats(ref, perf, AlignmentParams(band_width=2))
        self.assertEqual([b["ref_index"] for b in out["missed_beats"]], [2])
        self.assertEqual([b["perf_index"] for b in out["inserted_beats"]], [3])
        self.assertEqual(out["statistics"]["matched_count"], 5)
        # 匹配上的参考索引不应包含漏拍
        matched_refs = {b["ref_index"] for b in out["matched_beats"]}
        self.assertNotIn(2, matched_refs)
        matched_perfs = {b["perf_index"] for b in out["matched_beats"]}
        self.assertNotIn(3, matched_perfs)

    def test_band_too_narrow_raises(self):
        with self.assertRaises(AlignmentError):
            align_beats([0.0, 0.5, 1.0], [0.0], AlignmentParams(band_width=1))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            align_beats([], [0.0])

    def test_negative_penalty_raises(self):
        with self.assertRaises(ValueError):
            align_beats(
                [0.0, 0.5], [0.0, 0.5],
                AlignmentParams(skip_penalty=-1.0),
            )

    def test_rms_and_mean_consistency(self):
        ref = [0.0, 1.0, 2.0]
        perf = [0.1, 1.1, 2.1]
        out = align_beats(ref, perf)
        stats = out["statistics"]
        self.assertAlmostEqual(stats["mean_deviation"], 0.1, places=6)
        self.assertAlmostEqual(stats["rms_deviation"], 0.1, places=6)
        self.assertTrue(math.isfinite(stats["total_cost"]))


if __name__ == "__main__":
    unittest.main()
