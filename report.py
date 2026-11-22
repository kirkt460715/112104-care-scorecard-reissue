"""月报发报命令行入口（只读台账，输出 JSON）。

示例：
  python3 report.py compute --period 2026-09
  python3 report.py route --route A1 --period 2026-09
  python3 report.py correction --period 2026-09 --before 2026-10-04 --after 2026-10-12
  python3 report.py trace --contract C01 --period 2026-09
"""
from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import (
    build_contract_trace,
    build_correction_plan,
    build_route_ledger,
    build_route_report,
    compute,
    cross_route_consistency,
    load_data,
    parse_date,
)


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_default))


def _default(obj):
    from datetime import date
    if isinstance(obj, date):
        return obj.isoformat()
    return str(obj)


def main() -> None:
    parser = ArgumentParser(description="上门服务月报发报引擎")
    sub = parser.add_subparsers(dest="command", required=True)

    common = ArgumentParser(add_help=False)
    common.add_argument("--period", default="2026-09", help="服务月 YYYY-MM")
    common.add_argument("--as-of", dest="as_of", help="复算截止日 YYYY-MM-DD")

    sub.add_parser("compute", parents=[common], help="全量复算并输出口径、合计与整改建议")

    route_p = sub.add_parser("route", parents=[common], help="按收报路由装配报告")
    route_p.add_argument("--route", required=True)

    corr_p = sub.add_parser("correction", parents=[common], help="跨站回单更正/撤回计划")
    corr_p.add_argument("--before", required=True)
    corr_p.add_argument("--after", required=True)

    trace_p = sub.add_parser("trace", parents=[common], help="单合同已算/已核准/已交付追踪")
    trace_p.add_argument("--contract", required=True)

    sub.add_parser("ledger", help="各收报目标投递与确认台账")
    sub.add_parser("consistency", parents=[common], help="跨收报方一致性核验")

    options = parser.parse_args()
    data = load_data()
    as_of = parse_date(options.as_of) if getattr(options, "as_of", None) else None

    if options.command == "compute":
        _print(compute(data, options.period, as_of).to_dict())
    elif options.command == "route":
        _print(build_route_report(data, compute(data, options.period, as_of), options.route))
    elif options.command == "correction":
        _print(build_correction_plan(
            data, options.period, parse_date(options.before), parse_date(options.after)))
    elif options.command == "trace":
        _print(build_contract_trace(
            data, compute(data, options.period, as_of), options.contract))
    elif options.command == "ledger":
        _print(build_route_ledger(data, as_of or data.latest_recorded_date()))
    elif options.command == "consistency":
        _print(cross_route_consistency(data, compute(data, options.period, as_of)))


if __name__ == "__main__":
    main()
