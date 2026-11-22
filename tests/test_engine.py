"""发报引擎业务规则测试。

覆盖：合同换版与组合口径、跨承接方协作、补签不迁移、撤销、整改解除/回退、
桥接、收发版本链、更正计划与跨收报方一致性。合成场景在测试内构造，不改原始台账。
"""
from __future__ import annotations

import unittest
from datetime import date

from engine.contracts import evaluate_contract
from engine.deliveries import (
    build_correction_plan,
    build_route_ledger,
    cross_route_consistency,
)
from engine.model import load_data
from engine.pipeline import compute
from engine.receipts import resolve_receipts
from engine.reports import build_contract_trace, build_route_report

DATA = load_data()
SEP = "2026-09"


class ReceiptRevisionTest(unittest.TestCase):
    def test_high_revision_wins_when_registered(self):
        current = {r.receipt: r for r in resolve_receipts(DATA, date(2026, 10, 12))}
        self.assertEqual(current["R224"].state, "revoked")
        self.assertEqual(current["R224"].verdict, "revoked")
        self.assertEqual(current["R221"].state, "signed")
        self.assertEqual(current["R221"].verdict, "valid")

    def test_future_revision_ignored_before_recorded(self):
        # 10/05 截止：R221 补签（10/10 登记）尚未出现，仍是 unsigned
        current = {r.receipt: r for r in resolve_receipts(DATA, date(2026, 10, 5))}
        self.assertEqual(current["R221"].verdict, "missing")

    def test_retroactive_keeps_service_month(self):
        current = {r.receipt: r for r in resolve_receipts(DATA, date(2026, 10, 10))}
        r221 = current["R221"]
        self.assertTrue(r221.retroactive)
        self.assertEqual(r221.service_day, date(2026, 9, 19))
        self.assertEqual(r221.recorded, date(2026, 10, 10))
        # 服务归属月不随补签迁移
        self.assertEqual((r221.service_day.year, r221.service_day.month), (2026, 9))


class ContractMeasureTest(unittest.TestCase):
    def metric(self, contract, period, as_of):
        receipts = resolve_receipts(DATA, as_of)
        results, orphan = evaluate_contract(DATA, contract, period, receipts)
        self.assertEqual(orphan, [])
        return {(m.version): m for m in results}

    def test_c01_bundle_all_requires_both_items(self):
        # 10/12：R224/R230 撤销后，E220/E222/E223 三个完整组合，目标 3 达标
        metrics = self.metric("C01", SEP, date(2026, 10, 12))
        m = metrics["V2"]
        self.assertEqual(m.unit, "bundle")
        self.assertEqual(m.achieved, 3)
        self.assertTrue(m.target_met)
        # 单项存在但缺另一项的组合不单独计
        self.assertIn("R223", m.exceptions["bundle_incomplete"])
        self.assertNotIn("R223", m.counted_receipts)

    def test_c01_version_selected_by_service_day(self):
        # 9/1 起 V2；9 月内 C01 全部按 V2 计量，不出现 V1
        metrics = self.metric("C01", SEP, date(2026, 10, 12))
        self.assertEqual(set(metrics), {"V2"})

    def test_cross_partner_collaboration_counts_when_both_evidence(self):
        metrics = self.metric("C01", SEP, date(2026, 10, 12))
        group = next(g for g in metrics["V2"].groups if g.key == "H51/E220")
        self.assertTrue(group.complete)
        self.assertEqual(group.partner_split, ("A", "B"))
        self.assertTrue(group.multi_partner)
        # R224 撤销后，E221 只剩 A 方的 bath：单方待补而非跨方各报一半
        broken = next(g for g in metrics["V2"].groups if g.key == "H52/E221")
        self.assertFalse(broken.complete)
        self.assertTrue(broken.awaiting_completion)
        self.assertFalse(broken.multi_partner)
        self.assertEqual(broken.partner_split, ("A",))

    def test_c04_bundle_never_completes_single_items(self):
        metrics = self.metric("C04", SEP, date(2026, 10, 12))
        m = metrics["V1"]
        self.assertEqual(m.achieved, 0)
        self.assertFalse(m.target_met)
        self.assertTrue(all(not g.complete for g in m.groups))

    def test_c03_household_distinct_and_dispute_excluded(self):
        metrics = self.metric("C03", SEP, date(2026, 10, 12))
        v2 = metrics["V2"]
        self.assertEqual(v2.unit, "household")
        self.assertEqual(v2.achieved, 4)  # H00/H10/H12/H20，R222 disputed 不计
        self.assertIn("R222", v2.exceptions["in_dispute"])

    def test_c02_task_each_counts_every_valid_receipt(self):
        metrics = self.metric("C02", SEP, date(2026, 10, 12))
        m = metrics["V1"]
        self.assertEqual(m.unit, "task")
        self.assertEqual(m.achieved, 7)
        self.assertIn("R033", m.exceptions["evidence_missing"])


class TimeTravelTest(unittest.TestCase):
    def achieved(self, as_of):
        comp = compute(DATA, SEP, as_of)
        return comp.metrics[("C01", "V2")].achieved

    def test_resignation_then_revocation_timeline(self):
        self.assertEqual(self.achieved(date(2026, 10, 4)), 4)   # R221 未补签
        self.assertEqual(self.achieved(date(2026, 10, 10)), 5)  # 补签成立
        self.assertEqual(self.achieved(date(2026, 10, 12)), 3)  # 撤销两张后回落

    def test_deterministic_recompute(self):
        a = compute(DATA, SEP, date(2026, 10, 12)).to_dict()
        b = compute(DATA, SEP, date(2026, 10, 12)).to_dict()
        self.assertEqual(a, b)


class BridgeTest(unittest.TestCase):
    def test_bridge_series_distinguishes_no_bridge_and_partial(self):
        comp = compute(DATA, SEP, date(2026, 10, 12))
        series = comp.bridge_series
        self.assertEqual(series["C03"]["method"], "no_bridge")
        self.assertFalse(series["C03"]["comparable"])
        self.assertEqual(series["C01"]["method"], "partial")
        self.assertEqual(series["C01"]["before_unit"], "visit")
        self.assertEqual(series["C01"]["after_unit"], "bundle")


class ProgressTest(unittest.TestCase):
    def test_q1_closed_sustains_while_evidence_holds(self):
        comp = compute(DATA, SEP, date(2026, 10, 12))
        q1 = comp.progress["Q1"]
        self.assertEqual(q1["registered_state"], "closed")
        self.assertEqual(q1["recommendation"], "sustain_closed")

    def test_q2_retained_and_q3_release(self):
        comp = compute(DATA, SEP, date(2026, 10, 12))
        self.assertEqual(comp.progress["Q2"]["recommendation"], "retain")
        self.assertEqual(comp.progress["Q3"]["recommendation"], "release")

    def test_closed_case_reopens_when_evidence_lost(self):
        # 构造合成数据集：把 C01 9 月两个完整组合的关键 stairs 回单全部撤销，
        # 使 bundle 达标线跌破，已解除的 Q1 必须显式回到待核准。
        from copy import deepcopy
        from engine.model import ReceiptRevision

        synthetic = deepcopy(DATA)
        extra = []
        for item in synthetic.receipts:
            if item.receipt in ("R221", "R226", "R228") and item.revision == 1:
                extra.append(ReceiptRevision(
                    receipt=item.receipt, revision=2, contract=item.contract,
                    household=item.household, event=item.event,
                    service_day=item.service_day, task=item.task, signer=item.signer,
                    state="revoked", recorded=date(2026, 10, 13)))
        synthetic.receipts.extend(extra)
        comp = compute(synthetic, SEP, date(2026, 10, 13))
        self.assertLess(comp.metrics[("C01", "V2")].achieved, 3)
        self.assertEqual(comp.progress["Q1"]["recommendation"], "reopen")
        self.assertFalse(comp.progress["Q1"]["evidence_sufficient"])


class DeliveryTest(unittest.TestCase):
    def test_latest_confirmed_revision_and_stale_ack(self):
        ledger = build_route_ledger(DATA, date(2026, 10, 12))
        a2 = ledger["A2"]
        self.assertEqual(a2["latest_confirmed_revision"], 2)
        rev1 = next(r for r in a2["revision_status"] if r["delivery_revision"] == 1)
        self.assertEqual(rev1["staleness"], "superseded_confirmed")

    def test_unclaimed_route_has_no_confirmed_revision(self):
        ledger = build_route_ledger(DATA, date(2026, 10, 12))
        self.assertIsNone(ledger["A4"]["latest_confirmed_revision"])
        self.assertTrue(ledger["A4"]["has_unclaimed"])

    def test_correction_only_touches_period_receipts_and_scoped_routes(self):
        plan = build_correction_plan(DATA, SEP,
                                     date(2026, 10, 4), date(2026, 10, 12))
        ids = {item["receipt"] for item in plan["affected"]["receipts"]}
        self.assertEqual(ids, {"R221", "R224", "R230"})
        routes = {a["route"]: a["action"] for a in plan["route_actions"]}
        self.assertEqual(routes.get("A1"), "correct")    # 通配，已确认
        self.assertEqual(routes.get("A2"), "correct")    # 已确认 rev2
        self.assertEqual(routes.get("A4"), "resend")     # unclaimed，无确认
        self.assertNotIn("A3", routes)                   # C04 本期未受影响

    def test_cross_route_no_contradiction(self):
        comp = compute(DATA, SEP, date(2026, 10, 12))
        result = cross_route_consistency(DATA, comp)
        self.assertEqual(result["contradictions"], [])


class RouteReportTest(unittest.TestCase):
    def test_partial_policy_gates_sending(self):
        comp = compute(DATA, SEP, date(2026, 10, 12))
        # A1 覆盖全部合同，C04 未达标 => 非完整报告，但允许部分发送
        a1 = build_route_report(DATA, comp, "A1")
        self.assertFalse(a1["coverage"]["complete"])
        self.assertEqual(a1["send_decision"], "partial_allowed")
        self.assertEqual(build_route_report(DATA, comp, "A2")["send_decision"],
                         "partial_allowed")
        # A3/A4 不接收部分报告且范围不完整 => 暂扣
        self.assertEqual(build_route_report(DATA, comp, "A3")["send_decision"],
                         "withhold_until_complete")
        self.assertEqual(build_route_report(DATA, comp, "A4")["send_decision"],
                         "withhold_until_complete")

    def test_contract_trace_links_measured_approval_delivery(self):
        comp = compute(DATA, SEP, date(2026, 10, 12))
        trace = build_contract_trace(DATA, comp, "C04")
        self.assertTrue(trace["approval"])
        self.assertTrue(any(r["has_unclaimed"] is False for r in trace["delivered"]))
        self.assertTrue(trace["next_actions"])


if __name__ == "__main__":
    unittest.main()
