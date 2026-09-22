"""算法核心单元测试（标准库 unittest，无需额外依赖）。"""

import math
import unittest

from app.alignment import (
    AlignmentError,
    AlignmentParams,
    align_beats,
    validate_anchors,
)


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

    # ---------- 锚点硬约束 ----------

    def test_no_anchors_returns_empty_list(self):
        out = align_beats([0.0, 1.0], [0.0, 1.0])
        self.assertEqual(out["anchors"], [])

    def test_anchor_forces_match_that_free_dtw_avoids(self):
        # 末拍严重错位：自由 DTW 选择“漏第 4 拍 + 插入第 4 音”
        ref = [0.0, 1.0, 2.0, 3.0]
        perf = [0.0, 1.0, 2.0, 10.0]
        params = AlignmentParams(band_width=2, skip_penalty=0.01, insert_penalty=0.01)
        free = align_beats(ref, perf, params)
        free_pairs = [(b["ref_index"], b["perf_index"]) for b in free["matched_beats"]]
        self.assertEqual(free_pairs, [(0, 0), (1, 1), (2, 2)])
        self.assertEqual([b["ref_index"] for b in free["missed_beats"]], [3])
        self.assertEqual([b["perf_index"] for b in free["inserted_beats"]], [3])

        # 锚点 (3,3) 强制末拍必须对角匹配
        out = align_beats(ref, perf, params, [(3, 3)])
        pairs = [(b["ref_index"], b["perf_index"]) for b in out["matched_beats"]]
        self.assertEqual(pairs, [(0, 0), (1, 1), (2, 2), (3, 3)])
        self.assertEqual(out["missed_beats"], [])
        self.assertEqual(out["inserted_beats"], [])
        self.assertEqual(len(out["anchors"]), 1)
        self.assertEqual(out["anchors"][0]["ref_index"], 3)
        self.assertEqual(out["anchors"][0]["perf_index"], 3)
        self.assertAlmostEqual(out["anchors"][0]["deviation"], 7.0, places=6)

    def test_anchors_segment_and_merge_path(self):
        # 长度差 2：漏拍 / 插入拍只能落在锚点隔出的各段内
        ref = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
        perf = [0.0, 1.0, 1.5, 2.5]
        params = AlignmentParams(band_width=3)
        out = align_beats(ref, perf, params, [(0, 0), (5, 3)])
        pairs = [(b["ref_index"], b["perf_index"]) for b in out["matched_beats"]]
        # 两端锚点都在匹配对中，路径覆盖全部两端点
        self.assertIn((0, 0), pairs)
        self.assertIn((5, 3), pairs)
        self.assertEqual(out["statistics"]["missed_count"], 2)
        self.assertEqual(out["statistics"]["inserted_count"], 0)
        # 匹配对严格单调（拼接后的完整路径合法）
        ri_seq = [ri for ri, _ in pairs]
        pj_seq = [pj for _, pj in pairs]
        self.assertEqual(ri_seq, sorted(ri_seq))
        self.assertEqual(pj_seq, sorted(pj_seq))
        self.assertEqual(len(set(ri_seq)), len(ri_seq))
        self.assertEqual(len(set(pj_seq)), len(pj_seq))

    def test_multiple_anchors_split_skips_per_segment(self):
        ref = [0.0, 1.0, 2.0, 3.0, 4.0]
        perf = [0.0, 2.0, 4.0]
        params = AlignmentParams(band_width=3)
        out = align_beats(ref, perf, params, [(0, 0), (2, 1), (4, 2)])
        pairs = {(b["ref_index"], b["perf_index"]) for b in out["matched_beats"]}
        self.assertEqual(pairs, {(0, 0), (2, 1), (4, 2)})
        self.assertEqual([b["ref_index"] for b in out["missed_beats"]], [1, 3])
        adopted = [(a["ref_index"], a["perf_index"]) for a in out["anchors"]]
        self.assertEqual(adopted, [(0, 0), (2, 1), (4, 2)])

    def test_endpoint_anchors(self):
        ref = [0.0, 1.0, 2.0]
        perf = [0.05, 1.05, 2.05]
        out = align_beats(ref, perf, AlignmentParams(band_width=2), [(0, 0), (2, 2)])
        pairs = {(b["ref_index"], b["perf_index"]) for b in out["matched_beats"]}
        self.assertIn((0, 0), pairs)
        self.assertIn((2, 2), pairs)
        self.assertEqual(len(out["anchors"]), 2)

    def test_anchor_result_includes_times(self):
        out = align_beats(
            [0.0, 0.5], [0.0, 0.52], AlignmentParams(), [(1, 1)]
        )
        anchor = out["anchors"][0]
        self.assertEqual(anchor["ref_time"], 0.5)
        self.assertAlmostEqual(anchor["perf_time"], 0.52, places=6)
        self.assertAlmostEqual(anchor["deviation"], 0.02, places=6)

    def test_validate_anchor_indexes_must_increase(self):
        with self.assertRaisesRegex(AlignmentError, "参考拍索引必须严格递增"):
            validate_anchors([(1, 0), (1, 2)], 5, 5, 3)
        with self.assertRaisesRegex(AlignmentError, "演奏音索引必须严格递增"):
            validate_anchors([(0, 1), (2, 1)], 5, 5, 3)

    def test_validate_anchor_out_of_range(self):
        with self.assertRaisesRegex(AlignmentError, "越界"):
            validate_anchors([(5, 0)], 5, 5, 3)
        with self.assertRaisesRegex(AlignmentError, "越界"):
            validate_anchors([(0, 5)], 5, 5, 3)

    def test_validate_anchor_outside_band(self):
        with self.assertRaisesRegex(AlignmentError, "带宽不可达"):
            validate_anchors([(0, 4)], 5, 5, 2)
        with self.assertRaisesRegex(AlignmentError, "带宽不可达"):
            validate_anchors([(4, 0)], 5, 5, 2)

    def test_align_with_invalid_anchors_raises(self):
        ref = [0.0, 1.0, 2.0]
        perf = [0.0, 1.0, 2.0]
        with self.assertRaises(AlignmentError):
            align_beats(ref, perf, AlignmentParams(band_width=1), [(0, 2)])
        with self.assertRaises(AlignmentError):
            align_beats(ref, perf, AlignmentParams(), [(3, 0)])

    def test_empty_and_none_anchors_equivalent(self):
        ref = [0.0, 1.0, 2.0]
        perf = [0.0, 1.0, 2.0]
        a = align_beats(ref, perf, anchors=None)
        b = align_beats(ref, perf, anchors=[])
        self.assertEqual(
            [(x["ref_index"], x["perf_index"]) for x in a["matched_beats"]],
            [(x["ref_index"], x["perf_index"]) for x in b["matched_beats"]],
        )
        self.assertEqual(a["anchors"], b["anchors"], [])


if __name__ == "__main__":
    unittest.main()
