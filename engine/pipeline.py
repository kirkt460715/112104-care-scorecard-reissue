"""发报复算编排：把回单口径、合同口径、整改与收发台账串成可交付结果。

所有入口都接受显式 as_of，同一批输入同一截止日可重复复算；输出字典可直接 JSON 化，
并带口径（basis）与覆盖声明（coverage）。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date

from .contracts import evaluate_contract, service_month
from .model import Dataset, month_bounds
from .receipts import (
    DISPUTED,
    MISSING,
    REVOKED,
    UNREGISTERED,
    VALID,
    CurrentReceipt,
    resolve_receipts,
)

UNITS = ("visit", "task", "household", "bundle")
UNIT_LABEL = {
    "visit": "上门次数",
    "task": "服务项（按次）",
    "household": "家庭",
    "bundle": "服务组合",
}
VERDICT_LABEL = {
    VALID: "已签收可计量",
    MISSING: "文书缺失",
    DISPUTED: "重复归属待核",
    REVOKED: "已撤销",
    UNREGISTERED: "截止日前未登记",
}
BRIDGE_LABEL = {"full": "可桥接", "partial": "部分可桥接", "no_bridge": "不可桥接"}


@dataclass
class Computation:
    as_of: date
    period: str
    current_receipts: list[CurrentReceipt]
    metrics: dict  # (contract, version) -> MetricResult
    out_of_scope: list
    totals: dict
    bridge_series: dict
    progress: dict
    coverage: dict
    basis: dict

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "period": self.period,
            "basis": self.basis,
            "coverage": self.coverage,
            "receipts": [_receipt_dict(item) for item in self.current_receipts],
            "metrics": [_metric_dict(metric) for metric in sorted(
                self.metrics.values(), key=lambda m: (m.contract, m.version))],
            "out_of_scope": [asdict(item) for item in self.out_of_scope],
            "totals": self.totals,
            "metric_bridge": self.bridge_series,
            "progress": self.progress,
        }


def _receipt_dict(item: CurrentReceipt) -> dict:
    return {
        "receipt": item.receipt,
        "revision": item.revision,
        "contract": item.contract,
        "household": item.household,
        "event": item.event,
        "service_day": item.service_day.isoformat(),
        "service_month": service_month(item.service_day),
        "task": item.task,
        "signer": item.signer,
        "state": item.state,
        "recorded": item.recorded.isoformat() if item.recorded else None,
        "verdict": item.verdict,
        "verdict_label": VERDICT_LABEL[item.verdict],
        "retroactive": item.retroactive,
    }


def _metric_dict(metric) -> dict:
    return {
        "contract": metric.contract,
        "version": metric.version,
        "street": metric.street,
        "partner": metric.partner,
        "period": metric.period,
        "unit": metric.unit,
        "unit_label": UNIT_LABEL[metric.unit],
        "recognition": metric.recognition,
        "required": list(metric.required),
        "target": metric.target,
        "achieved": metric.achieved,
        "target_met": metric.target_met,
        "counted_receipts": list(metric.counted_receipts),
        "groups": [asdict(group) for group in metric.groups],
        "exceptions": {key: sorted(set(values)) for key, values in metric.exceptions.items()},
        "bridge": metric.bridge,
        "bridge_label": BRIDGE_LABEL.get(metric.bridge) if metric.bridge else None,
    }


def compute(data: Dataset, period: str, as_of: date | None = None) -> Computation:
    as_of = as_of or data.latest_recorded_date()
    current = resolve_receipts(data, as_of)

    metrics: dict[tuple[str, str], object] = {}
    out_of_scope = []
    for contract_id in sorted(data.contract_versions):
        results, orphan = evaluate_contract(data, contract_id, period, current)
        out_of_scope.extend(orphan)
        for metric in results:
            metrics[(contract_id, metric.version)] = metric

    totals = _build_totals(metrics)
    bridge_series = _build_bridge_series(data, metrics)
    progress = _assess_progress(data, metrics, period, current)
    coverage = _build_coverage(current, metrics, out_of_scope, period)
    basis = {
        "as_of": as_of.isoformat(),
        "period": period,
        "version_rule": "按服务日适用的合同版本计量；补签不迁移服务月份与跨期目标",
        "receipt_rule": "取各回单 recorded<=as_of 的最高 revision 当前正文",
        "units": {unit: UNIT_LABEL[unit] for unit in UNITS},
    }
    return Computation(
        as_of=as_of,
        period=period,
        current_receipts=current,
        metrics=metrics,
        out_of_scope=out_of_scope,
        totals=totals,
        bridge_series=bridge_series,
        progress=progress,
        coverage=coverage,
        basis=basis,
    )


def _build_totals(metrics: dict) -> dict:
    """分街道、分承接方合计，并按合同服务单位对齐（不同单位不混加）。

    承接方给两个口径：合同归属方（合同 partner）与实际履约方（回单 signer），
    后者才能看见跨承接方协作中各出一半的情况。
    """
    by_street: dict[tuple[str, str], int] = defaultdict(int)
    by_owner: dict[tuple[str, str], int] = defaultdict(int)
    by_signer: dict[tuple[str, str], int] = defaultdict(int)
    by_contract: dict[str, list] = {}
    for metric in metrics.values():
        by_street[(metric.street, metric.unit)] += metric.achieved
        by_owner[(metric.partner, metric.unit)] += metric.achieved
        by_contract.setdefault(metric.contract, []).append(metric)
        # 实际履约方：按被计入的回单 signer 归因；组合按整组成果在参与方间不重复加总，
        # 这里仅统计服务项凭证的实际出具方，用于协作可见性。
        for outcome in metric.outcomes:
            if outcome.counted:
                by_signer[(outcome.signer, metric.unit)] += 1

    return {
        "by_street": [
            {"street": street, "unit": unit, "unit_label": UNIT_LABEL[unit], "achieved": value}
            for (street, unit), value in sorted(by_street.items())
        ],
        "by_contract_partner": [
            {"partner": partner, "unit": unit, "unit_label": UNIT_LABEL[unit], "achieved": value}
            for (partner, unit), value in sorted(by_owner.items())
        ],
        "by_service_signer": [
            {"signer": signer, "unit": unit, "unit_label": UNIT_LABEL[unit], "counted_items": value}
            for (signer, unit), value in sorted(by_signer.items())
        ],
        "by_contract": {
            contract: [
                {
                    "version": metric.version,
                    "unit": metric.unit,
                    "achieved": metric.achieved,
                    "target": metric.target,
                    "target_met": metric.target_met,
                }
                for metric in versions
            ]
            for contract, versions in sorted(by_contract.items())
        },
    }


def _build_bridge_series(data: Dataset, metrics: dict) -> dict:
    """换版序列：区分可复算历史基准、不可桥接指标与真实服务变化。

    单位取自合同版本元数据而非当月指标，保证换版月即使旧版当月无数据也能说明口径。
    """
    series = {}
    for bridge in data.bridges:
        before = metrics.get((bridge.contract, bridge.before))
        after = metrics.get((bridge.contract, bridge.after))
        before_contract = data.contract_version(bridge.contract, bridge.before)
        after_contract = data.contract_version(bridge.contract, bridge.after)
        before_unit = before_contract.unit if before_contract else None
        after_unit = after_contract.unit if after_contract else None
        series[bridge.contract] = {
            "before": bridge.before,
            "after": bridge.after,
            "method": bridge.method,
            "method_label": BRIDGE_LABEL[bridge.method],
            "before_unit": before_unit,
            "after_unit": after_unit,
            "before_unit_label": UNIT_LABEL.get(before_unit),
            "after_unit_label": UNIT_LABEL.get(after_unit),
            "before_in_period": before is not None,
            "after_in_period": after is not None,
            "comparable": bridge.method != "no_bridge" and before_unit == after_unit,
            "note": _bridge_note(bridge.method, before_unit, after_unit),
        }
    return series


def _bridge_note(method: str, before_unit, after_unit) -> str:
    if method == "no_bridge":
        return "口径不可桥接，前后序列断开，差异不得解释为服务变化"
    if before_unit != after_unit:
        return "计量单位变化，仅可在有服务明细凭证的范围内复算"
    if method == "partial":
        return "仅可在具备服务明细凭证的范围内复算历史基准"
    return "可在历史材料上完整复算"


def _assess_progress(data: Dataset, metrics: dict, period: str,
                     current: list[CurrentReceipt]) -> dict:
    """用能支撑的分项给整改核准建议；登记事实与引擎建议分开。

    一个合同同月跨两版时逐版给出达标判断，整改以服务发生时适用版本为准，
    任一适用版本未达标都不能解除。
    """
    cycle_id = data.cycle_for_period(period)
    result = {}
    for case in data.progress_cases:
        if case.cycle != cycle_id:
            continue
        contract_metrics = sorted(
            (m for m in metrics.values() if m.contract == case.contract),
            key=lambda m: m.version)
        version_evidence = [
            {
                "version": m.version,
                "unit": m.unit,
                "achieved": m.achieved,
                "target": m.target,
                "target_met": m.target_met,
            }
            for m in contract_metrics
        ]
        # 跨承接方各报一半待查：组合未完成、已有一方凭证且仍缺服务项
        split_pending = []
        incomplete_groups = []
        for metric in contract_metrics:
            for group in metric.groups:
                if group.complete:
                    continue
                incomplete_groups.append(f"{metric.version}:{group.key}")
                if group.awaiting_completion:
                    split_pending.append({
                        "group": f"{metric.version}:{group.key}",
                        "partners": list(group.partner_split),
                        "multi_partner": group.multi_partner,
                        "missing_items": list(group.missing_items),
                        "blockers": list(group.blockers),
                    })
        evidence_sufficient = bool(contract_metrics) and all(
            item["target_met"] for item in version_evidence)
        latest = contract_metrics[-1] if contract_metrics else None
        if case.state == "closed":
            if evidence_sufficient:
                recommendation = "sustain_closed"
                note = "解除所依据的证据在当前截止日仍然成立，维持已解除"
            else:
                recommendation = "reopen"
                note = "已解除整改失去证据支撑，须显式回到待核准"
        else:
            recommendation = "release" if evidence_sufficient else "retain"
            note = ("各适用版本证据均达目标线，可解除整改" if evidence_sufficient
                    else "存在未达目标线的适用版本，保留整改范围")
        result[case.case] = {
            "case": case.case,
            "contract": case.contract,
            "cycle": case.cycle,
            "registered_state": case.state,
            "opened": case.opened.isoformat(),
            "release": case.release.isoformat() if case.release else None,
            "version_evidence": version_evidence,
            "achieved": latest.achieved if latest else None,
            "target": latest.target if latest else None,
            "unit": latest.unit if latest else None,
            "evidence_sufficient": evidence_sufficient,
            "incomplete_groups": incomplete_groups,
            "split_pending": split_pending,
            "recommendation": recommendation,
            "note": note,
        }
    return result


def _build_coverage(current: list[CurrentReceipt], metrics: dict,
                    out_of_scope: list, period: str) -> dict:
    start, end = month_bounds(period)
    in_month = [r for r in current if start <= r.service_day < end]
    buckets = defaultdict(list)
    for receipt in in_month:
        buckets[receipt.verdict].append(receipt.receipt)
    return {
        "period": period,
        "total_receipts": len(in_month),
        "counted": sorted(
            rid for metric in metrics.values() for rid in metric.counted_receipts),
        "by_verdict": {key: sorted(set(values)) for key, values in buckets.items()},
        "out_of_scope": [item.receipt for item in out_of_scope],
        "statement": "本报告仅覆盖截止日前已登记正文；文书缺失、待核、撤销与未登记项均列示不计数",
    }
