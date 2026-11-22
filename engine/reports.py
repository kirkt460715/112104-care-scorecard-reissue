"""按收报订阅装配报告视图，并提供单合同的"已算/已核准/已交付"追踪。"""
from __future__ import annotations

from .contracts import EVIDENCE_MISSING, IN_DISPUTE, WITHDRAWN, BUNDLE_INCOMPLETE
from .deliveries import build_route_ledger
from .model import Dataset
from .pipeline import UNIT_LABEL, Computation


def _route_allows(audience, contract_id: str, street: str) -> bool:
    if audience.street and audience.street != street:
        return False
    if audience.contracts and contract_id not in audience.contracts:
        return False
    return True


def build_route_report(data: Dataset, computation: Computation, route: str) -> dict:
    audience = next(item for item in data.audiences if item.route == route)
    ledger = build_route_ledger(data, computation.as_of)[route]

    selected = []
    missing_in_scope = []
    for metric in sorted(computation.metrics.values(),
                         key=lambda m: (m.contract, m.version)):
        if not _route_allows(audience, metric.contract, metric.street):
            continue
        selected.append(metric)
        for bucket in (EVIDENCE_MISSING, IN_DISPUTE, WITHDRAWN, BUNDLE_INCOMPLETE):
            for receipt_id in metric.exceptions.get(bucket, []):
                missing_in_scope.append({
                    "contract": metric.contract,
                    "receipt": receipt_id,
                    "issue": bucket,
                })

    body = _view_body(audience.view, selected, computation)
    complete = not missing_in_scope and all(
        metric.target_met for metric in selected)
    can_send = complete or audience.partials
    return {
        "route": route,
        "recipient": audience.recipient,
        "view": audience.view,
        "as_of": computation.as_of.isoformat(),
        "period": computation.period,
        "basis": computation.basis,
        "coverage": {
            "complete": complete,
            "partial_allowed": audience.partials,
            "open_items": missing_in_scope,
            "statement": computation.coverage["statement"],
        },
        "delivery": {
            "latest_confirmed_revision": ledger["latest_confirmed_revision"],
            "has_pending_ack": ledger["has_pending_ack"],
            "has_unclaimed": ledger["has_unclaimed"],
            "revision_status": ledger["revision_status"],
        },
        "can_send": can_send,
        "send_decision": "full" if complete else (
            "partial_allowed" if can_send else "withhold_until_complete"),
        "body": body,
    }


def _view_body(view: str, selected, computation: Computation) -> dict:
    if view == "street_activity":
        # 基层视角：按次完成，逐回单可查
        return {
            "metrics": [_compact_metric(m) for m in selected],
            "receipt_level": [
                {
                    "receipt": o.receipt,
                    "contract": o.contract,
                    "version": o.version,
                    "task": o.task,
                    "signer": o.signer,
                    "service_day": o.service_day.isoformat(),
                    "disposition": o.disposition,
                    "counted": o.counted,
                    "retroactive": o.retroactive,
                }
                for m in selected for o in m.outcomes
            ],
        }
    if view == "metric_bridge":
        return {
            "metrics": [_compact_metric(m) for m in selected],
            "bridge": {
                key: value for key, value in computation.bridge_series.items()
                if any(m.contract == key for m in selected)
            },
        }
    # contract_progress：采购视角，按服务组合看承诺兑现
    return {
        "metrics": [_compact_metric(m) for m in selected],
        "groups": [
            {
                "contract": g.contract,
                "group": g.key,
                "partners": list(g.partner_split),
                "present": g.present,
                "missing_items": list(g.missing_items),
                "blockers": list(g.blockers),
                "complete": g.complete,
                "counted": g.counted,
            }
            for m in selected for g in m.groups
        ],
    }


def _compact_metric(metric) -> dict:
    return {
        "contract": metric.contract,
        "version": metric.version,
        "unit": metric.unit,
        "unit_label": UNIT_LABEL[metric.unit],
        "recognition": metric.recognition,
        "required": list(metric.required),
        "achieved": metric.achieved,
        "target": metric.target,
        "target_met": metric.target_met,
        "bridge": metric.bridge,
        "exceptions": {k: sorted(set(v)) for k, v in metric.exceptions.items()},
    }


def build_contract_trace(data: Dataset, computation: Computation,
                         contract_id: str) -> dict:
    """跟着一个合同看清：已算（计量）、已核准（整改）、已交付（收发）。"""
    metrics = [m for m in computation.metrics.values() if m.contract == contract_id]
    cases = [value for value in computation.progress.values()
             if value["contract"] == contract_id]
    routes = []
    ledger = build_route_ledger(data, computation.as_of)
    for audience in data.audiences:
        if not audience.contracts or contract_id in audience.contracts:
            route = ledger[audience.route]
            routes.append({
                "route": audience.route,
                "recipient": audience.recipient,
                "latest_confirmed_revision": route["latest_confirmed_revision"],
                "has_pending_ack": route["has_pending_ack"],
                "has_unclaimed": route["has_unclaimed"],
            })
    return {
        "contract": contract_id,
        "period": computation.period,
        "as_of": computation.as_of.isoformat(),
        "measured": [_compact_metric(m) for m in metrics],
        "approval": cases,
        "delivered": routes,
        "next_actions": _next_actions(metrics, cases, routes),
    }


def _next_actions(metrics, cases, routes) -> list[dict]:
    actions = []
    for metric in metrics:
        for bucket, owner in (
            (EVIDENCE_MISSING, "责任承接方补文书"),
            (IN_DISPUTE, "双方核重复归属"),
        ):
            for receipt_id in metric.exceptions.get(bucket, []):
                actions.append({"item": receipt_id, "action": owner})
        for group in metric.groups:
            if not group.complete:
                actions.append({
                    "item": group.key,
                    "action": f"待补 {','.join(group.missing_items) or '阻塞凭证'}",
                })
    for case in cases:
        if case["recommendation"] in ("retain", "reopen"):
            actions.append({"item": case["case"], "action": "保留/回到待核准，等待证据"})
        elif case["recommendation"] == "release":
            actions.append({"item": case["case"], "action": "证据达标，由负责人确认解除"})
    for route in routes:
        if route["has_pending_ack"]:
            actions.append({"item": route["route"], "action": "继续查询收报确认回执"})
        if route["has_unclaimed"]:
            actions.append({"item": route["route"], "action": "unclaimed 投递需重发或核实"})
    return actions
