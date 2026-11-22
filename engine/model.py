"""数据装载与领域模型。

约定：
- 登记时间 recorded 与服务发生月 service_day 始终分开使用；补签不迁移服务月份。
- 回单主键为 (receipt, revision)，同一 receipt 的高 revision 取代低 revision 状态，
  但只有在某个截止日已经登记（recorded <= as_of）的正文才参与该日的复算。
- 空字段表示未填写或不适用。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    return date.fromisoformat(value)


def parse_month(value: str) -> tuple[int, int]:
    year, month = value.split("-")
    return int(year), int(month)


def month_bounds(period: str) -> tuple[date, date]:
    """返回服务月的半开区间 [月初, 下月初)。"""
    year, month = parse_month(period)
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def split_list(value: str) -> list[str]:
    return [item for item in (value or "").split("|") if item]


@dataclass(frozen=True)
class Contract:
    contract: str
    version: str
    street: str
    partner: str
    valid_from: date
    valid_to: date
    unit: str
    required: tuple[str, ...]
    recognition: str  # either（分别认可）/ all（组合全完成才算）/ each（逐项计量）
    target: int

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day < self.valid_to


@dataclass(frozen=True)
class ReceiptRevision:
    receipt: str
    revision: int
    contract: str
    household: str
    event: str
    service_day: date
    task: str
    signer: str
    state: str
    recorded: date


@dataclass(frozen=True)
class Bridge:
    contract: str
    before: str
    after: str
    method: str  # full / partial / no_bridge


@dataclass(frozen=True)
class Cycle:
    cycle: str
    period: str
    deadline: date


@dataclass(frozen=True)
class Audience:
    route: str
    recipient: str
    street: str
    contracts: tuple[str, ...]  # 空表示 *
    view: str
    partials: bool


@dataclass(frozen=True)
class Delivery:
    delivery: str
    attempt: int
    route: str
    cycle: str
    delivery_revision: int
    received_scope: tuple[str, ...]
    sent_on: date


@dataclass(frozen=True)
class DeliveryReceipt:
    delivery: str
    acknowledged: date
    result: str


@dataclass(frozen=True)
class ProgressCase:
    case: str
    contract: str
    cycle: str
    opened: date
    release: date | None
    state: str  # 登记事实：open / closed


@dataclass
class Dataset:
    contracts: list[Contract]
    receipts: list[ReceiptRevision]
    bridges: list[Bridge]
    cycles: list[Cycle]
    audiences: list[Audience]
    deliveries: list[Delivery]
    delivery_receipts: list[DeliveryReceipt]
    progress_cases: list[ProgressCase]
    # 派生索引
    contract_versions: dict[str, list[Contract]] = field(default_factory=dict)

    def contract_at(self, contract: str, day: date) -> Contract | None:
        """业务发生时适用的合同解释：按服务日落在哪个版本区间选择。"""
        for version in self.contract_versions.get(contract, []):
            if version.applies_on(day):
                return version
        return None

    def latest_recorded_date(self) -> date:
        return max(item.recorded for item in self.receipts)

    def cycle_for_period(self, period: str) -> str | None:
        """服务月 -> 发报周期（report_cycles 的 cycle 字段）。"""
        for cycle in self.cycles:
            if cycle.period == period:
                return cycle.cycle
        return None

    def contract_version(self, contract: str, version: str) -> Contract | None:
        for item in self.contract_versions.get(contract, []):
            if item.version == version:
                return item
        return None


def _read(name: str) -> list[dict[str, str]]:
    with (DATA / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_data() -> Dataset:
    contracts = [
        Contract(
            contract=row["contract"],
            version=row["version"],
            street=row["street"],
            partner=row["partner"],
            valid_from=parse_date(row["from"]),
            valid_to=parse_date(row["to"]),
            unit=row["unit"],
            required=tuple(split_list(row["required"])),
            recognition=row["recognition"],
            target=int(row["target"]),
        )
        for row in _read("contracts")
    ]
    receipts = [
        ReceiptRevision(
            receipt=row["receipt"],
            revision=int(row["revision"]),
            contract=row["contract"],
            household=row["household"],
            event=row["event"],
            service_day=parse_date(row["service_day"]),
            task=row["task"],
            signer=row["signer"],
            state=row["state"],
            recorded=parse_date(row["recorded"]),
        )
        for row in _read("receipts")
    ]
    bridges = [
        Bridge(row["contract"], row["before"], row["after"], row["method"])
        for row in _read("metric_bridge")
    ]
    cycles = [
        Cycle(row["cycle"], row["period"], parse_date(row["deadline"]))
        for row in _read("report_cycles")
    ]
    audiences = [
        Audience(
            route=row["route"],
            recipient=row["recipient"],
            street="" if row["street"] == "*" else row["street"],
            contracts=tuple(item for item in split_list(row["contracts"]) if item != "*"),
            view=row["view"],
            partials=row["partials"] == "yes",
        )
        for row in _read("audiences")
    ]
    deliveries = [
        Delivery(
            delivery=row["delivery"],
            attempt=int(row["attempt"]),
            route=row["route"],
            cycle=row["cycle"],
            delivery_revision=int(row["delivery_revision"]),
            received_scope=tuple(split_list(row["received_scope"])),
            sent_on=parse_date(row["sent_on"]),
        )
        for row in _read("deliveries")
    ]
    delivery_receipts = [
        DeliveryReceipt(row["delivery"], parse_date(row["acknowledged"]), row["result"])
        for row in _read("delivery_receipts")
    ]
    progress_cases = [
        ProgressCase(
            case=row["case"],
            contract=row["contract"],
            cycle=row["cycle"],
            opened=parse_date(row["opened"]),
            release=parse_date(row["release"]),
            state=row["state"],
        )
        for row in _read("progress_cases")
    ]

    contract_versions: dict[str, list[Contract]] = {}
    for contract in contracts:
        contract_versions.setdefault(contract.contract, []).append(contract)
    for versions in contract_versions.values():
        versions.sort(key=lambda item: item.valid_from)

    return Dataset(
        contracts=contracts,
        receipts=receipts,
        bridges=bridges,
        cycles=cycles,
        audiences=audiences,
        deliveries=deliveries,
        delivery_receipts=delivery_receipts,
        progress_cases=progress_cases,
        contract_versions=contract_versions,
    )
