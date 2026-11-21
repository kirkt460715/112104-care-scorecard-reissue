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

## 交换表

- `contracts.csv`。合同适用期间、街道和合作单位。复合服务口径明确要求列出的各项是否需要都兑现。 主键由 `contract`、`version` 确定。
- `receipts.csv`。服务回单。event 为同一户已有确认的上门关系，disputed 为重复归属待核。 主键由 `receipt`、`revision` 确定。
- `metric_bridge.csv`。采购部门登记哪些口径可以在历史材料上重算。partial 意味只能给出有相应服务明细凭证的范围。 主键由 `contract`、`before`、`after` 确定。
- `report_cycles.csv`。定期发报日和期间。 主键由 `cycle` 确定。
- `audiences.csv`。收报用途、范围和是否可接收明确覆盖范围的部分报告。 主键由 `route` 确定。
- `deliveries.csv`。留存于独立收发台账的发送尝试。更新重复投递沿用同一版本和标识。 主键由 `delivery`、`attempt` 确定。
- `delivery_receipts.csv`。收到报告服务方返回的真实收报确证，可能晚到。 主键由 `delivery`、`acknowledged` 确定。
- `progress_cases.csv`。责任方已有整改工单中的开始、解除事实。 主键由 `case` 确定。
