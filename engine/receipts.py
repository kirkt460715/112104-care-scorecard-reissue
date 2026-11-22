"""回单口径：在某个截止日把 (receipt, revision) 折叠成当前可计量证据。

口径要点：
- 只看 recorded <= as_of 的修订；高 revision 且已登记的正文取代旧状态。
- signed 是可计量证据；unsigned 属于文书缺失；disputed 是重复归属待核；
  revoked 是已撤销、不能计量。
- 服务归属月份始终看 service_day，补签（recorded 晚于服务月）不迁移服务。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .model import Dataset, ReceiptRevision

# 证据裁决（回单当前正文层面，尚不含合同是否认可）
VALID = "valid"          # 已签收，可计量
MISSING = "missing"      # 文书缺失（未签收）
DISPUTED = "disputed"    # 重复归属待核
REVOKED = "revoked"      # 已撤销
UNREGISTERED = "unregistered"  # 截止日前没有任何已登记正文

_STATE_VERDICT = {
    "signed": VALID,
    "unsigned": MISSING,
    "disputed": DISPUTED,
    "revoked": REVOKED,
}


@dataclass(frozen=True)
class CurrentReceipt:
    receipt: str
    revision: int | None
    contract: str
    household: str
    event: str
    service_day: date
    task: str
    signer: str
    state: str | None
    recorded: date | None
    verdict: str
    retroactive: bool  # 补签：成为有效证据的登记时间晚于服务所在月之后


def resolve_receipts(data: Dataset, as_of: date) -> list[CurrentReceipt]:
    latest: dict[str, ReceiptRevision] = {}
    for item in data.receipts:
        if item.recorded > as_of:
            continue
        current = latest.get(item.receipt)
        if current is None or item.revision > current.revision:
            latest[item.receipt] = item

    result: list[CurrentReceipt] = []
    # 未在截止日前登记的回单也要显式呈现，避免被静默漏掉
    registered_ids = set(latest)
    for item in data.receipts:
        if item.receipt in registered_ids:
            continue
        result.append(
            CurrentReceipt(
                receipt=item.receipt,
                revision=None,
                contract=item.contract,
                household=item.household,
                event=item.event,
                service_day=item.service_day,
                task=item.task,
                signer=item.signer,
                state=None,
                recorded=None,
                verdict=UNREGISTERED,
                retroactive=False,
            )
        )

    for item in latest.values():
        verdict = _STATE_VERDICT[item.state]
        retroactive = (
            verdict == VALID
            and (item.recorded.year, item.recorded.month)
            != (item.service_day.year, item.service_day.month)
        )
        result.append(
            CurrentReceipt(
                receipt=item.receipt,
                revision=item.revision,
                contract=item.contract,
                household=item.household,
                event=item.event,
                service_day=item.service_day,
                task=item.task,
                signer=item.signer,
                state=item.state,
                recorded=item.recorded,
                verdict=verdict,
                retroactive=retroactive,
            )
        )
    result.sort(key=lambda item: (item.service_day, item.receipt))
    return result
