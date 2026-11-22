"""上门服务月报发报引擎。

只读 data/ 下登记的交换表，不修改任何台账。所有计算都可以用同一批输入、
同一个截止日（as_of）复算，输出带口径与覆盖声明的结构化结果。
"""
from .model import load_data, parse_date, split_list  # noqa: F401
from .pipeline import compute  # noqa: F401
from .reports import build_contract_trace, build_route_report  # noqa: F401
from .deliveries import (  # noqa: F401
    build_correction_plan,
    build_route_ledger,
    cross_route_consistency,
)

__all__ = [
    "load_data",
    "parse_date",
    "split_list",
    "compute",
    "build_route_report",
    "build_contract_trace",
    "build_correction_plan",
    "build_route_ledger",
    "cross_route_consistency",
]
