# 上门服务绩效口径换版与报表更正

服务采购月报所使用的签收回单、合同计量口径和收报交换台账。 所有人员、服务机构和业务凭证均为合成资料。

登记时间与服务发生月分别使用。回单 revision 较高且已经登记的正文取代旧回单状态。月报发送与收报是两方独立确认的事实。delivery_revision 每个收报目标内递增。投递标识未收到确认时应当继续查询凭证。

## 资料查阅

需要 Python 3.11.2，查看资料与运行资料检查只用标准库。CSV 使用 UTF-8，首行为字段名。空字段表示该项未填写或不适用，具体含义按各表约定。

```sh
python3 catalog.py
python3 -m unittest discover -s tests -v
```

例如，`python3 catalog.py --table contracts` 可读取该表原始记录。资料检查核对结构、唯一键及部分业务引用。

## 发报引擎

`report.py` 在只读台账上复算月报，同一批输入、同一截止日（`--as-of`）结果可重复。
所有输出为带口径（basis）与覆盖声明（coverage）的 JSON，不写入任何资料表。

```sh
python3 report.py compute --period 2026-09                 # 全量复算
python3 report.py compute --period 2026-09 --as-of 2026-10-04
python3 report.py route --route A2 --period 2026-09        # 按收报路由装配
python3 report.py correction --period 2026-09 --before 2026-10-04 --after 2026-10-12
python3 report.py trace --contract C01 --period 2026-09    # 已算/已核准/已交付
python3 report.py ledger                                   # 投递与确认台账
python3 report.py consistency --period 2026-09             # 跨收报方一致性
```

口径要点（代码位于 `engine/`）：

- **单位**：个人、家庭（household）、服务组合（bundle）、上门次数（visit）/服务项（task）各有单位，合计按单位对齐，不混加。
- **适用版本**：以服务日落在哪个合同版本区间选择解释，补签只改变证据是否成立，不迁移服务月份或跨期目标。
- **recognition**：`each`/`either` 按可确认服务项分别认可；`all` 要求列出的各项都兑现，整组才算一个组合，跨承接方协作按回单中能确认到服务项的凭证判定，出具方（signer）用于标注协作与待查。
- **证据裁决**：signed 可计量；unsigned 文书缺失、disputed 重复归属待核、revoked 已撤销，均列示但不计数；截止日前未登记正文单列。
- **换版桥接**：`metric_bridge` 的 `partial` 仅在有服务明细凭证范围内复算历史基准，`no_bridge` 前后序列断开、差异不得解释为真实服务变化。
- **整改核准**：登记事实（open/closed）与引擎建议（retain/release/sustain_closed/reopen）分开；已解除整改若失去证据会显式建议 reopen。
- **收发**：每个收报目标内只有被确认过的最高 `delivery_revision` 算已确认最新；晚到的旧版本确认标记 superseded，未确认旧投递不得当最新；unclaimed 需继续查询。
- **更正**：只识别服务月落在该报告期内、且在两个截止日间正文变化的回单，按实际受影响合同与路由订阅范围决定 correct/resend。
- **一致性**：所有路由共享同一套 canonical 回单裁决，同一回单不会在一处算履约、另一处确定不算；范围差异由订阅口径决定并在抬头声明。

## 交换表

- `contracts.csv`。合同适用期间、街道和合作单位。复合服务口径明确要求列出的各项是否需要都兑现。 主键由 `contract`、`version` 确定。
- `receipts.csv`。服务回单。event 为同一户已有确认的上门关系，disputed 为重复归属待核。 主键由 `receipt`、`revision` 确定。
- `metric_bridge.csv`。采购部门登记哪些口径可以在历史材料上重算。partial 意味只能给出有相应服务明细凭证的范围。 主键由 `contract`、`before`、`after` 确定。
- `report_cycles.csv`。定期发报日和期间。 主键由 `cycle` 确定。
- `audiences.csv`。收报用途、范围和是否可接收明确覆盖范围的部分报告。 主键由 `route` 确定。
- `deliveries.csv`。留存于独立收发台账的发送尝试。更新重复投递沿用同一版本和标识。 主键由 `delivery`、`attempt` 确定。
- `delivery_receipts.csv`。收到报告服务方返回的真实收报确证，可能晚到。 主键由 `delivery`、`acknowledged` 确定。
- `progress_cases.csv`。责任方已有整改工单中的开始、解除事实。 主键由 `case` 确定。
