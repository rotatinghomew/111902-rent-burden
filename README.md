# 季度租住调查资料

本地合成业务资料，不含真实住户或机构信息。仓库提供抽样框、访谈、租赁凭据、片区争议、调查回执及拨款记录，供离线分析流程使用。

`data/supplies.json` 按业务表保存记录，`data/fields.json` 记录各表字段及资料中出现的值类型。`frame` 记录各季住户抽样单位、片区、分层和两阶段入样概率；重复住房可能对应不同住户。`interviews` 保存收入区间，缺失端点表示没有收入证据。`contracts` 是租约事实，`reports` 是承租人或代理的申报行，`claims` 给出可能的片区归属与租金份额。租金份额受同一合同总额约束。`standards` 记录地区固定分层权重、覆盖及比较标准；`receipts` 可提供新份额或撤销旧证据。`requests`、`ledger` 和 `budgets` 是申请、已签收/预留金额及资金池，`inquiries` 和 `work_roster` 是调查任务与可用工时。

使用 Linux、Python 3.11 及标准库运行 `python3 -m unittest discover -s tests -v`，检查数据字段、类型和关键关联。资料仓库不包含分析结果或办理入口。
