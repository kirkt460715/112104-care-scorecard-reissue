"""合同计量口径。

业务发生时适用的合同版本由服务日决定（见 ``Dataset.contract_at``），补签只改变
证据是否成立，不改变服务归属期，也不迁移跨期目标。

三种 recognition：
- each：每个能确认到服务项的凭证分别计一次，单位多为 task/household/visit。
- either：组合内任一项兑现即认可，按可确认服务项分别计。
- all：列出的各项必须都兑现，整组才算一个履约单位（bundle），跨承接方协作时
  只要各服务项都有可确认凭证即可，凭证上的承接方（signer）用于标注协作与待查。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .model import Contract, Dataset, month_bounds
from .receipts import CurrentReceipt, DISPUTED, MISSING, REVOKED, UNREGISTERED, VALID

# 回单在当前合同口径下的处理结论
COUNTED = "counted"          # 计入履约
EVIDENCE_MISSING = "evidence_missing"  # 文书缺失，不能计
IN_DISPUTE = "in_dispute"    # 重复归属待核，不能计
WITHDRAWN = "withdrawn"      # 已撤销，不能计
NOT_YET_REGISTERED = "not_yet_registered"
BUNDLE_INCOMPLETE = "bundle_incomplete"  # 组合缺项，单项不计
OUT_OF_SCOPE = "out_of_scope"  # 服务日没有适用合同版本

_VERDICT_DISPOSITION = {
    MISSING: EVIDENCE_MISSING,
    DISPUTED: IN_DISPUTE,
    REVOKED: WITHDRAWN,
    UNREGISTERED: NOT_YET_REGISTERED,
}


@dataclass
class ReceiptOutcome:
    receipt: str
    revision: int | None
    contract: str
    version: str | None
    household: str
    event: str
    service_day: date
    service_month: str
    task: str
    signer: str
    verdict: str
    disposition: str
    counted: bool
    retroactive: bool
    note: str = ""


@dataclass
class GroupStatus:
    key: str
    contract: str
    version: str
    period: str
    household: str
    event: str
    required: tuple[str, ...]
    present: dict[str, list[str]] = field(default_factory=dict)   # 服务项 -> 回单
    missing_items: tuple[str, ...] = ()
    partner_split: tuple[str, ...] = ()       # 已出具可确认凭证的承接方
    multi_partner: bool = False               # 是否涉及多个承接方协作
    awaiting_completion: bool = False         # 已有部分凭证、组合仍未完整（待补/待查）
    complete: bool = False
    counted: bool = False
    blockers: tuple[str, ...] = ()


@dataclass
class MetricResult:
    contract: str
    version: str
    street: str
    partner: str
    period: str
    unit: str
    recognition: str
    required: tuple[str, ...]
    target: int
    achieved: int
    target_met: bool
    counted_receipts: tuple[str, ...]
    outcomes: list[ReceiptOutcome]
    groups: list[GroupStatus]
    exceptions: dict[str, list[str]]
    bridge: str | None  # full / partial / no_bridge，仅非首版有值


def service_month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _bridge_for(data: Dataset, contract: str, version: str) -> str | None:
    for bridge in data.bridges:
        if bridge.contract == contract and bridge.after == version:
            return bridge.method
    return None


def make_out_of_scope(receipt: CurrentReceipt) -> ReceiptOutcome:
    return _outcome(receipt, None, OUT_OF_SCOPE, False, "服务日无适用合同版本")


def _outcome(receipt: CurrentReceipt, version: str | None, disposition: str,
             counted: bool, note: str) -> ReceiptOutcome:
    return ReceiptOutcome(
        receipt=receipt.receipt,
        revision=receipt.revision,
        contract=receipt.contract,
        version=version,
        household=receipt.household,
        event=receipt.event,
        service_day=receipt.service_day,
        service_month=service_month(receipt.service_day),
        task=receipt.task,
        signer=receipt.signer,
        verdict=receipt.verdict,
        disposition=disposition,
        counted=counted,
        retroactive=receipt.retroactive,
        note=note,
    )


def evaluate_contract(
    data: Dataset,
    contract_id: str,
    period: str,
    receipts: list[CurrentReceipt],
) -> tuple[list[MetricResult], list[ReceiptOutcome]]:
    """按服务日适用版本，把一个合同一个服务月的回单逐版计量。

    过渡月（换版落在月中）会产生同一合同的多个版本结果；无适用版本的回单
    单独作为越界结果返回，保证完整记录里没有静默丢失。
    """
    start, end = month_bounds(period)
    in_month = [r for r in receipts if r.contract == contract_id and start <= r.service_day < end]

    by_version: dict[str, list[CurrentReceipt]] = defaultdict(list)
    out_of_scope: list[ReceiptOutcome] = []
    for receipt in in_month:
        contract = data.contract_at(contract_id, receipt.service_day)
        if contract is None:
            out_of_scope.append(make_out_of_scope(receipt))
        else:
            by_version[contract.version].append(receipt)

    results = [
        _evaluate_version(data, contract_id, period, version, items)
        for version, items in sorted(by_version.items())
    ]
    return results, out_of_scope


def _evaluate_version(
    data: Dataset,
    contract_id: str,
    period: str,
    version: str,
    receipts: list[CurrentReceipt],
) -> MetricResult:
    contract = next(
        item for item in data.contract_versions[contract_id] if item.version == version
    )
    outcomes: list[ReceiptOutcome] = []
    groups: list[GroupStatus] = []
    counted_receipts: list[str] = []
    exceptions: dict[str, list[str]] = defaultdict(list)

    if contract.recognition == "all":
        groups = _build_groups(contract, period, receipts)
        counted_ids: set[str] = set()
        for group in groups:
            if group.complete:
                group.counted = True
                counted_ids.update(rid for rids in group.present.values() for rid in rids)
            else:
                for rid in [rid for rids in group.present.values() for rid in rids]:
                    exceptions[BUNDLE_INCOMPLETE].append(rid)
        for receipt in receipts:
            if receipt.verdict != VALID:
                outcomes.append(_outcome(
                    receipt, version, _VERDICT_DISPOSITION[receipt.verdict], False,
                    _evidence_note(receipt)))
                _record_exception(exceptions, receipt)
            else:
                is_counted = receipt.receipt in counted_ids
                note = "组合完整，计入履约" if is_counted else "组合缺项，单项不单独计"
                outcomes.append(_outcome(
                    receipt, version, COUNTED if is_counted else BUNDLE_INCOMPLETE,
                    is_counted, note))
                if is_counted:
                    counted_receipts.append(receipt.receipt)
        achieved = sum(1 for group in groups if group.counted)
    else:
        # each / either：每个有效凭证按服务项分别认可
        for receipt in receipts:
            if receipt.verdict == VALID:
                outcomes.append(_outcome(receipt, version, COUNTED, True, "服务项凭证可确认"))
                counted_receipts.append(receipt.receipt)
            else:
                outcomes.append(_outcome(
                    receipt, version, _VERDICT_DISPOSITION[receipt.verdict], False,
                    _evidence_note(receipt)))
                _record_exception(exceptions, receipt)
        if contract.unit == "household":
            achieved = len({r.household for r in receipts if r.verdict == VALID})
        else:
            achieved = sum(1 for r in receipts if r.verdict == VALID)

    return MetricResult(
        contract=contract_id,
        version=version,
        street=contract.street,
        partner=contract.partner,
        period=period,
        unit=contract.unit,
        recognition=contract.recognition,
        required=contract.required,
        target=contract.target,
        achieved=achieved,
        target_met=achieved >= contract.target,
        counted_receipts=tuple(sorted(counted_receipts)),
        outcomes=outcomes,
        groups=groups,
        exceptions=dict(exceptions),
        bridge=_bridge_for(data, contract_id, version),
    )


def _evidence_note(receipt: CurrentReceipt) -> str:
    return {
        MISSING: "文书缺失，未形成可计量证据",
        DISPUTED: "重复归属待核，暂不计量",
        REVOKED: "已撤销回单",
        UNREGISTERED: "截止日前尚无已登记正文",
    }[receipt.verdict]


_EXCEPTION_BUCKET = {
    MISSING: EVIDENCE_MISSING,
    DISPUTED: IN_DISPUTE,
    REVOKED: WITHDRAWN,
    UNREGISTERED: NOT_YET_REGISTERED,
}


def _record_exception(exceptions: dict[str, list[str]], receipt: CurrentReceipt) -> None:
    exceptions[_EXCEPTION_BUCKET[receipt.verdict]].append(receipt.receipt)


def _build_groups(
    contract: Contract, period: str, receipts: list[CurrentReceipt]
) -> list[GroupStatus]:
    grouped: dict[tuple[str, str], list[CurrentReceipt]] = defaultdict(list)
    for receipt in receipts:
        grouped[(receipt.household, receipt.event)].append(receipt)

    groups: list[GroupStatus] = []
    for (household, event), items in sorted(grouped.items()):
        present: dict[str, list[str]] = defaultdict(list)
        partners: set[str] = set()
        blockers: list[str] = []
        for item in items:
            if item.verdict == VALID:
                present[item.task].append(item.receipt)
                partners.add(item.signer)
            else:
                blockers.append(f"{item.receipt}:{item.task}:{item.verdict}")
        missing = tuple(task for task in contract.required if task not in present)
        complete = not missing and not blockers
        # 已有可确认凭证但组合未完整：单方待补或跨承接方各报一半的待查情形
        awaiting = (not complete) and bool(present)
        groups.append(
            GroupStatus(
                key=f"{household}/{event}",
                contract=contract.contract,
                version=contract.version,
                period=period,
                household=household,
                event=event,
                required=contract.required,
                present=dict(present),
                missing_items=missing,
                partner_split=tuple(sorted(partners)),
                multi_partner=len(partners) > 1,
                awaiting_completion=awaiting,
                complete=complete,
                counted=False,
                blockers=tuple(blockers),
            )
        )
    return groups
