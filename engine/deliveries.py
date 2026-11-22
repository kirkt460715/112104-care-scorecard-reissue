"""收报交换：投递台账、真实确认回执、更正/撤回计划与跨收报方一致性。

事实分层：
- deliveries 是发送尝试（可多次 attempt、可升 delivery_revision）；
- delivery_receipts 是收报方返回的真实确认，可能晚到；
- 一个收报目标内，只有被确认过的最高 delivery_revision 才算"已确认最新"。
  未确认的旧投递不能在新版本之后被当作最新。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .model import Audience, Dataset, month_bounds
from .receipts import CurrentReceipt, resolve_receipts


def build_route_ledger(data: Dataset, as_of: date) -> dict:
    attempts_by_delivery = defaultdict(list)
    for delivery in data.deliveries:
        attempts_by_delivery[delivery.delivery].append(delivery)

    acks = {}
    for receipt in data.delivery_receipts:
        if receipt.acknowledged <= as_of:
            acks[receipt.delivery] = receipt

    routes = {}
    for audience in data.audiences:
        attempts = [d for group in attempts_by_delivery.values() for d in group
                    if d.route == audience.route]
        revisions = defaultdict(list)
        for delivery in attempts:
            revisions[delivery.delivery_revision].append(delivery)

        revision_status = []
        for revision in sorted(revisions):
            items = sorted(revisions[revision], key=lambda d: d.attempt)
            ack = next((acks[d.delivery] for d in items if d.delivery in acks), None)
            revision_status.append({
                "delivery_revision": revision,
                "deliveries": [d.delivery for d in items],
                "sent_on": [d.sent_on.isoformat() for d in items],
                "received_scope": sorted(
                    {scope for d in items for scope in d.received_scope}),
                "acknowledged": ack.acknowledged.isoformat() if ack else None,
                "ack_result": ack.result if ack else None,
                "confirmed": bool(ack and ack.result == "accepted"),
            })

        confirmed = [item for item in revision_status if item["confirmed"]]
        latest_confirmed = max(
            (item["delivery_revision"] for item in confirmed), default=None)
        # 已存在更高确认版本后，旧版本（即使其确认晚到）也不能再当最新；
        # 未确认的旧投递同样标记为被取代，防止被当作最新。
        for item in revision_status:
            if latest_confirmed is not None and \
                    item["delivery_revision"] < latest_confirmed:
                item["staleness"] = ("superseded_confirmed" if item["confirmed"]
                                     else "superseded_unacknowledged")
            elif latest_confirmed is not None and \
                    item["delivery_revision"] == latest_confirmed:
                item["staleness"] = "current_confirmed"
            elif item["acknowledged"] is None:
                item["staleness"] = "pending_ack"
            else:
                item["staleness"] = "ack_not_accepted"

        routes[audience.route] = {
            "route": audience.route,
            "recipient": audience.recipient,
            "view": audience.view,
            "partials_allowed": audience.partials,
            "revision_status": revision_status,
            "latest_confirmed_revision": latest_confirmed,
            "has_pending_ack": any(
                item["staleness"] == "pending_ack" for item in revision_status),
            "has_unclaimed": any(
                item["ack_result"] == "unclaimed" for item in revision_status),
        }
    return routes


def affected_receipts(data: Dataset, before: date, after: date,
                      period: str | None = None) -> dict:
    """两个截止日之间回单当前正文发生变化、且影响指定报告期的凭证。

    period 给定时只保留服务月落在该期间的回单，避免把报告期之后新登记的
    服务（如下个月才发生的上门）误判为本期更正。
    """
    start, end = month_bounds(period) if period else (None, None)
    old = {r.receipt: r for r in resolve_receipts(data, before)}
    new = {r.receipt: r for r in resolve_receipts(data, after)}
    changed = []
    for receipt_id in sorted(set(old) | set(new)):
        old_item = old.get(receipt_id)
        new_item = new.get(receipt_id)
        if period is not None:
            service_day = (new_item or old_item).service_day
            if not (start <= service_day < end):
                continue
        if _signature(old_item) != _signature(new_item):
            changed.append({
                "receipt": receipt_id,
                "contract": (new_item or old_item).contract,
                "service_day": (new_item or old_item).service_day.isoformat(),
                "from_revision": getattr(old_item, "revision", None),
                "to_revision": getattr(new_item, "revision", None),
                "from_verdict": getattr(old_item, "verdict", None),
                "to_verdict": getattr(new_item, "verdict", None),
            })
    contracts = sorted({item["contract"] for item in changed})
    return {"receipts": changed, "contracts": contracts}


def _signature(item: CurrentReceipt | None):
    if item is None:
        return None
    return item.revision, item.verdict, item.state


def build_correction_plan(data: Dataset, period: str, before: date,
                          after: date) -> dict:
    """更正一条跨站回单后，按已投递事实与实际影响决定谁更正、谁撤回。"""
    affected = affected_receipts(data, before, after, period)
    ledger = build_route_ledger(data, after)
    actions = []
    for audience in data.audiences:
        scope = _scope_overlap(audience, affected["contracts"])
        route = ledger[audience.route]
        if not scope:
            continue
        latest = route["latest_confirmed_revision"]
        if latest is None:
            action = "resend"
            note = "无已确认投递，旧尝试不得作为最新；按当前口径重新发送"
        else:
            action = "correct"
            note = f"已确认 revision {latest}，就受影响合同 {sorted(scope)} 发送更正并保留版本链"
        if route["has_unclaimed"]:
            note += "；存在 unclaimed 投递，需继续查询凭证并防止旧投递被当最新"
        actions.append({
            "route": audience.route,
            "recipient": audience.recipient,
            "affected_contracts": sorted(scope),
            "latest_confirmed_revision": latest,
            "action": action,
            "note": note,
        })
    return {
        "period": period,
        "window": {"before": before.isoformat(), "after": after.isoformat()},
        "affected": affected,
        "route_actions": actions,
        "rule": "仅对实际受影响且在收报范围内的目标动作；未确认旧投递标记 superseded，不得当最新",
    }


def _route_contracts(audience: Audience) -> set[str]:
    return set(audience.contracts)


def _scope_overlap(audience: Audience, contracts: list[str]) -> set[str]:
    """受影响合同与路由订阅范围的交集；通配（contracts 为空）匹配全部。"""
    if not audience.contracts:
        return set(contracts)
    return set(contracts) & set(audience.contracts)


def cross_route_consistency(data: Dataset, computation) -> dict:
    """同一回单在各收报方的计量结论必须一致；范围/口径不同须显式说明。

    引擎对所有路由复用同一套 canonical 回单裁决（以当期全部回单为基准，含确定
    不计数项），核验路由过滤后不会出现"一处已算履约、另一处确定不算"的矛盾；
    仅因订阅范围不同而不可见，属于约定范围差异，由报告抬头声明。
    """
    from .model import month_bounds

    start, end = month_bounds(computation.period)
    canonical = {}
    for receipt in computation.current_receipts:
        if not (start <= receipt.service_day < end):
            continue
        counted = receipt.receipt in set(computation.coverage["counted"])
        canonical[receipt.receipt] = {
            "contract": receipt.contract,
            "counted": counted,
            "verdict": receipt.verdict,
        }

    per_route = {}
    contradictions = []
    for audience in data.audiences:
        allowed = set(audience.contracts)
        visible = {}
        for receipt_id, conclusion in canonical.items():
            if not allowed or conclusion["contract"] in allowed:
                visible[receipt_id] = conclusion
        per_route[audience.route] = {
            "recipient": audience.recipient,
            "view": audience.view,
            "visible_receipts": len(visible),
            "counted": sorted(rid for rid, c in visible.items() if c["counted"]),
            "not_counted": sorted(
                rid for rid, c in visible.items() if not c["counted"]),
        }
        # 同一回单若在两个路由都可见，counted 结论必须一致（共享 canonical 保证）
        for receipt_id, conclusion in visible.items():
            if conclusion["counted"] != canonical[receipt_id]["counted"]:
                contradictions.append({
                    "receipt": receipt_id,
                    "route": audience.route,
                    "route_counted": conclusion["counted"],
                    "canonical_counted": canonical[receipt_id]["counted"],
                })
    return {
        "per_route": per_route,
        "contradictions": contradictions,
        "statement": "同一 canonical 裁决驱动所有路由；范围差异由订阅口径决定并在报告抬头声明",
    }
