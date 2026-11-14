# 产品数据与调用约定

events 的 occurred 为闭区间 [earliest, latest]，区间相交不定义先后，received_at 只用于材料可见性。对象类型 order、parcel、truck。links 的 split 与 replacement 包含 units，补发指向失败包裹，合车只代表共享运输，不合并订单身份。rules 中的 allowed 与 quantity_policy 是业务事实，不是推荐实现。POST /conformance {case, cutoff} 返回 {snapshot, cases, patterns, shared_events}；cases 至少包括 order、status、evidence，status 使用 conforming/deviating/indeterminate/open。解释须引用 event_id，不能把未观察签收直接判为违法流程。patterns 的计数单位必须说明。

当前 GET /cases、POST /preview 和 GET /health 已可用；client.analysis 是待接入的业务消费调用，新分析路由尚不存在。原始数据通过 app.store 读取，snapshot 为输入字节的 SHA-256。新增接口须保留现有预览能力和 JSON 有限数值约定。

cases 是记录列表；共享事件保留唯一 event_id 引用。截尾期间缺少未来动作不是既成偏离。permuted 只改变文件行顺序，不改变证据的部分序。新增输出包含 diagnostics，说明无法判定的分支和使用的符合性语义。
