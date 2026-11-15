"""流程符合性分析：对象中心事件 + 部分序语义。

判定口径（与 docs/contracts.md 一致）：
- occurred 为闭区间，仅当 left.occurred[1] < right.occurred[0] 时认定先后；
  区间相交不定义先后，不能据文件行序或 received_at 推断因果。
- 订单状态：conforming / deviating / indeterminate / open。
  open 表示观察截止时仍在进行，缺少未来动作不是既成偏离；
  indeterminate 表示关键事件缺失或顺序歧义，无法判定；
  deviating 只用于能定位到具体 event_id 的真实违反。
- 数量守恒按订单闭环：拆出 = 订购，补发消耗失败件数
  （rules.quantity_policy == replacement_consumes_failed_units）。
- 合车只代表共享运输：共享事件在 shared_events 中按 event_id 唯一保留，
  商户聚合时同一 event_id 只计一次，不按订单重复计为独立故障。
"""
from collections import defaultdict

from app.fulfillment import object_events
from app.rules import certain_before
from app.store import unique

# 数量守恒项的业务含义，供偏离解释引用。
QUANTITY_RULES = (
    'split_units_match_order',
    'replacement_units_match_failed',
    'parcel_units_match_link',
    'replacement_target_is_failed',
    'replacement_authorized_for_link',
    'delivered_units_match_order',
    'awaiting_outcome',
    'unresolved_units',
)


def _link_indexes(case):
    splits = defaultdict(list)        # source order -> split links
    replacements = defaultdict(list)  # source parcel -> replacement links
    trucks_of = defaultdict(list)     # parcel -> truck ids
    for link in case['links']:
        if link['kind'] == 'split':
            splits[link['source']].append(link)
        elif link['kind'] == 'replacement':
            replacements[link['source']].append(link)
        elif link['kind'] == 'truck':
            trucks_of[link['source']].append(link['target'])
    return splits, replacements, trucks_of


def _order_objects(case, order_id):
    """订单的包裹族谱（split/replacement 前向闭包）与涉及车辆。"""
    parcels = set()
    frontier = [order_id]
    while frontier:
        source = frontier.pop()
        for link in case['links']:
            if link['kind'] in ('split', 'replacement') and link['source'] == source:
                if link['target'] not in parcels:
                    parcels.add(link['target'])
                    frontier.append(link['target'])
    trucks = {link['target'] for link in case['links']
              if link['kind'] == 'truck' and link['source'] in parcels}
    return parcels, trucks


def _kinds(events):
    grouped = defaultdict(list)
    for event in events:
        grouped[event['activity']].append(event)
    return grouped


def _first(events):
    return min(events, key=lambda event: (event['occurred'][0], event['event_id']))


def _check_required(order_events, parcels, trucks_of, visible):
    """逐条检查 rules.required 的先后约束。

    返回 (violations, indeterminate, open_items)，每项为 (rule, detail, evidence)。
    """
    violations, indeterminate, open_items = [], [], []
    accepted = [e for e in order_events if e['activity'] == 'accepted']
    accept = _first(accepted) if accepted else None
    if accept is None:
        open_items.append(('accepted_before_pack', '订单尚未观察到接单事件', []))

    for parcel in sorted(parcels):
        kinds = _kinds(visible.get(parcel, []))
        packs = kinds.get('packed', [])
        outcomes = kinds.get('delivered', []) + kinds.get('damaged', [])
        if not packs:
            if outcomes:
                # 已到达运输结果（终态）却始终没有打包事件：正向证据，记为偏离。
                violations.append(('parcel_lifecycle',
                                   '包裹 ' + parcel + ' 已出现运输结果但观察窗口内无打包事件',
                                   [e['event_id'] for e in outcomes]))
            else:
                open_items.append(('accepted_before_pack', '包裹 ' + parcel + ' 尚未观察到打包', []))
            continue
        pack = _first(packs)
        if accept is None:
            # 未观察到接单却已打包：正向证据，记为偏离。
            violations.append(('accepted_before_pack',
                               '包裹 ' + parcel + ' 已打包但订单未观察到接单',
                               [e['event_id'] for e in packs]))
        elif certain_before(pack, accept):
            violations.append(('accepted_before_pack',
                               '包裹 ' + parcel + ' 的打包确定早于订单接单',
                               [pack['event_id'], accept['event_id']]))
        elif not certain_before(accept, pack):
            indeterminate.append(('accepted_before_pack',
                                  '接单与包裹 ' + parcel + ' 打包的时间区间相交，先后无法判定',
                                  [accept['event_id'], pack['event_id']]))

        trucks = trucks_of.get(parcel, [])
        departures = [e for truck in trucks for e in visible.get(truck, [])
                      if e['activity'] == 'departed']
        if not trucks:
            if outcomes:
                violations.append(('pack_before_departure',
                                   '包裹 ' + parcel + ' 未关联车辆却出现运输结果',
                                   [e['event_id'] for e in outcomes]))
            else:
                open_items.append(('pack_before_departure', '包裹 ' + parcel + ' 尚未装车', []))
            continue
        if not departures:
            if outcomes:
                violations.append(('pack_before_departure',
                                   '包裹 ' + parcel + ' 所在车辆未观察到发车却有运输结果',
                                   [e['event_id'] for e in outcomes]))
            else:
                open_items.append(('pack_before_departure', '包裹 ' + parcel + ' 所在车辆尚未发车', []))
            continue
        depart = _first(departures)
        if certain_before(depart, pack):
            violations.append(('pack_before_departure',
                               '车辆发车确定早于包裹 ' + parcel + ' 打包完成',
                               [depart['event_id'], pack['event_id']]))
        elif not certain_before(pack, depart):
            indeterminate.append(('pack_before_departure',
                                  '包裹 ' + parcel + ' 打包与发车区间相交，先后无法判定',
                                  [pack['event_id'], depart['event_id']]))
        for outcome in outcomes:
            if certain_before(outcome, depart):
                violations.append(('depart_before_delivery',
                                   '包裹 ' + parcel + ' 的' + outcome['activity'] + '确定早于车辆发车',
                                   [outcome['event_id'], depart['event_id']]))
            elif not certain_before(depart, outcome):
                indeterminate.append(('depart_before_delivery',
                                      '发车与包裹 ' + parcel + ' 的' + outcome['activity']
                                      + '区间相交，先后无法判定',
                                      [depart['event_id'], outcome['event_id']]))
    return violations, indeterminate, open_items


def _check_quantities(case, order, parcels, splits, replacements, visible):
    """数量守恒检查。返回 (violations, indeterminate, open_items)。"""
    violations, indeterminate, open_items = [], [], []
    order_id = order['id']
    parcel_units = {row['id']: row['units'] for row in case['parcels']}
    link_by_target = {}
    for link in splits[order_id]:
        link_by_target[link['target']] = link
    for source in parcels:
        for link in replacements[source]:
            link_by_target[link['target']] = link

    if splits[order_id]:
        split_total = sum(link['units'] for link in splits[order_id])
        if split_total != order['units']:
            violations.append(('split_units_match_order',
                               '拆出件数 %d 与订单件数 %d 不一致' % (split_total, order['units']),
                               []))
    else:
        open_items.append(('split_units_match_order', '订单尚未拆箱', []))

    delivered_units = 0
    replaced_units = 0
    for parcel in sorted(parcels):
        kinds = _kinds(visible.get(parcel, []))
        if parcel in parcel_units and parcel in link_by_target \
                and link_by_target[parcel]['units'] != parcel_units[parcel]:
            link = link_by_target[parcel]
            violations.append(('parcel_units_match_link',
                               '包裹 %s 容量 %d 与关系件数 %d 不一致'
                               % (parcel, parcel_units[parcel], link['units']), []))
        if kinds.get('damaged'):
            failed = parcel_units.get(parcel, 0)
            for link in replacements[parcel]:
                if link['units'] != failed:
                    violations.append(('replacement_units_match_failed',
                                       '补发件数 %d 与失败包裹 %s 件数 %d 不一致'
                                       % (link['units'], parcel, failed),
                                       [e['event_id'] for e in kinds['damaged']]))
                replaced_units += link['units']
        if kinds.get('delivered'):
            delivered_units += parcel_units.get(parcel, 0)
        # 重复打包：同一包裹两次 packed 必须严格先后，否则是并行重复打包。
        packs = sorted(kinds.get('packed', []), key=lambda e: (e['occurred'][0], e['event_id']))
        if len(packs) > 1 and not all(certain_before(a, b) for a, b in zip(packs, packs[1:])):
            violations.append(('parcel_lifecycle',
                               '包裹 ' + parcel + ' 存在区间相交的重复打包',
                               [e['event_id'] for e in packs]))

    # 补发是对失败包裹的条件分支：源包裹未失败且分支无活动时不是发现。
    # 一旦分支有正向活动（授权或补发包裹开始流转）而源包裹无失败事件，
    # 或补发包裹已流转而缺少授权，均记为偏离并引用对应事件。
    for source in sorted(parcels):
        for link in replacements[source]:
            source_kinds = _kinds(visible.get(source, []))
            damaged = source_kinds.get('damaged', [])
            authorized = [e for e in visible.get(source, [])
                          if e['activity'] == 'replacement_authorized'
                          and link['target'] in e['objects'] and source in e['objects']]
            target_activity = [e for e in visible.get(link['target'], [])
                               if e['activity'] != 'replacement_authorized']
            if not damaged:
                evidence = [e['event_id'] for e in authorized + target_activity]
                if evidence:
                    violations.append(('replacement_target_is_failed',
                                       '补发分支 %s->%s 已活动但源包裹未观察到失败事件'
                                       % (source, link['target']), evidence))
            elif not authorized and target_activity:
                violations.append(('replacement_authorized_for_link',
                                   '补发包裹 ' + link['target'] + ' 已流转但缺少覆盖 '
                                   + source + ' 的授权事件',
                                   [e['event_id'] for e in target_activity]))
            elif not authorized:
                open_items.append(('replacement_authorized_for_link',
                                   '包裹 ' + source + ' 已失败，补发 ' + link['target']
                                   + ' 尚未观察到授权', []))

    # 签收守恒：只在每个包裹都有确定结局后判定，否则保持 open。
    unresolved = [p for p in sorted(parcels)
                  if not _kinds(visible.get(p, [])).get('delivered')
                  and not _kinds(visible.get(p, [])).get('damaged')]
    for parcel in unresolved:
        if visible.get(parcel):
            open_items.append(('awaiting_outcome',
                               '包裹 ' + parcel + ' 已流转但截止时无签收或失败结果', []))
    if splits[order_id] and not unresolved:
        if delivered_units != order['units']:
            violations.append(('delivered_units_match_order',
                               '签收件数 %d 与订单件数 %d 不一致' % (delivered_units, order['units']),
                               []))
    elif splits[order_id]:
        missing = order['units'] - delivered_units - replaced_units
        if missing > 0:
            open_items.append(('unresolved_units',
                               '%d 件既未签收也未进入补发闭环' % missing, []))
    return violations, indeterminate, open_items


def _check_allowed(case, visible, involved):
    """活动白名单：事件活动必须属于其对象类型的 allowed 集合。"""
    type_of = {}
    for kind, key in (('order', 'orders'), ('parcel', 'parcels'), ('truck', 'trucks')):
        for row in case[key]:
            type_of[row['id']] = kind
    allowed = case['rules'].get('allowed', {})
    violations = []
    seen = set()
    for identifier in sorted(involved):
        for event in visible.get(identifier, []):
            if event['event_id'] in seen:
                continue
            seen.add(event['event_id'])
            for obj in event['objects']:
                kind = type_of.get(obj)
                if kind and event['activity'] not in allowed.get(kind, []):
                    violations.append(('allowed_activity',
                                       '活动 %s 不在 %s 类对象的允许集合内' % (event['activity'], kind),
                                       [event['event_id']]))
                    break
    return violations


def _analyze_order(case, order, visible, splits, replacements, trucks_of):
    order_id = order['id']
    parcels, trucks = _order_objects(case, order_id)
    violations, indeterminate, open_items = [], [], []

    if case['rules'].get('required'):
        v, i, o = _check_required(visible.get(order_id, []), parcels, trucks_of, visible)
        violations += v
        indeterminate += i
        open_items += o
    v, i, o = _check_quantities(case, order, parcels, splits, replacements, visible)
    violations += v
    indeterminate += i
    open_items += o
    violations += _check_allowed(case, visible, parcels | trucks | {order_id})

    if violations:
        status = 'deviating'
    elif indeterminate:
        status = 'indeterminate'
    elif open_items:
        status = 'open'
    else:
        status = 'conforming'

    def entries(items, kind):
        return [{'kind': kind, 'rule': rule, 'detail': detail, 'evidence': evidence}
                for rule, detail, evidence in items]

    return {
        'order': order_id,
        'merchant': order.get('merchant'),
        'status': status,
        'findings': entries(violations, 'violation') + entries(indeterminate, 'indeterminate')
        + entries(open_items, 'open'),
        'evidence': sorted({e for _, _, ev in violations + indeterminate for e in ev}),
        'branches': {
            'parcels': sorted(parcels),
            'replacements': [{'source': link['source'], 'target': link['target'],
                              'units': link['units']}
                             for parcel in sorted(parcels) for link in replacements[parcel]],
            'trucks': sorted(trucks),
        },
    }


def _shared_events(case, rows, visible):
    """跨订单共享事件：同一 event_id 出现在多张订单的对象上。

    合车带来的公共事件只保留唯一 event_id 引用并列出受影响订单，
    聚合时不按订单重复计数。
    """
    by_id = {event['event_id']: event for event in case['events']}
    owners = defaultdict(set)
    for row in rows:
        involved = set(row['branches']['parcels']) | set(row['branches']['trucks']) | {row['order']}
        for identifier in involved:
            for event in visible.get(identifier, []):
                owners[event['event_id']].add(row['order'])
    return [{'event_id': event_id, 'activity': by_id[event_id]['activity'],
             'orders': sorted(orders)}
            for event_id, orders in sorted(owners.items()) if len(orders) > 1]


def _patterns(rows, shared):
    """按商户聚合偏离与返工负担。

    计数单位在 unit 字段说明：deviation_findings 以“订单×规则”为单位；
    rework_parcels 以补发包裹为单位；shared_events_unique 按 event_id 去重，
    同一车带来的公共事件不在每张订单里重复计为独立故障。
    """
    merchants = defaultdict(lambda: {'orders': 0, 'conforming': 0, 'deviating': 0,
                                     'indeterminate': 0, 'open': 0, 'deviation_findings': 0,
                                     'rework_parcels': 0, 'by_rule': defaultdict(int)})
    for row in rows:
        bucket = merchants[row['merchant']]
        bucket['orders'] += 1
        bucket[row['status']] += 1
        bucket['rework_parcels'] += len(row['branches']['replacements'])
        for finding in row['findings']:
            if finding['kind'] == 'violation':
                bucket['deviation_findings'] += 1
                bucket['by_rule'][finding['rule']] += 1
    shared_by_merchant = defaultdict(set)
    for item in shared:
        for row in rows:
            if row['order'] in item['orders']:
                shared_by_merchant[row['merchant']].add(item['event_id'])
    return {merchant: {
        'orders': bucket['orders'],
        'status_counts': {key: bucket[key] for key in ('conforming', 'deviating', 'indeterminate', 'open')},
        'deviation_findings': bucket['deviation_findings'],
        'by_rule': dict(sorted(bucket['by_rule'].items())),
        'rework_parcels': bucket['rework_parcels'],
        'shared_events_unique': len(shared_by_merchant[merchant]),
        'unit': {'deviation_findings': 'order_rule_findings',
                 'rework_parcels': 'replacement_parcels',
                 'shared_events_unique': 'distinct_event_ids'},
    } for merchant, bucket in sorted(merchants.items())}


def conformance(case, cutoff=None):
    cutoff = case['cutoff'] if cutoff is None else cutoff
    if cutoff > case['cutoff']:
        raise ValueError('cutoff beyond observed horizon')
    unique(case['orders'], 'id')
    unique(case['parcels'], 'id')
    unique(case['trucks'], 'id')
    splits, replacements, trucks_of = _link_indexes(case)
    visible = object_events(case, cutoff)

    rows = [_analyze_order(case, order, visible, splits, replacements, trucks_of)
            for order in case['orders']]
    shared = _shared_events(case, rows, visible)
    diagnostics = [{'order': row['order'], 'rule': finding['rule'],
                    'detail': finding['detail'], 'evidence': finding['evidence']}
                   for row in rows for finding in row['findings']
                   if finding['kind'] == 'indeterminate']
    if not diagnostics:
        diagnostics.append({'order': None, 'rule': 'none',
                            'detail': '本次观察窗口内没有无法判定的分支', 'evidence': []})
    return {
        'snapshot': case['_snapshot'],
        'cutoff': cutoff,
        'cases': rows,
        'patterns': _patterns(rows, shared),
        'shared_events': shared,
        'diagnostics': diagnostics,
        'semantics': {
            'order': 'certain_before: 仅当 occurred 区间严格先后才认定因果；区间相交记为 indeterminate',
            'evidence': 'deviating 需要正向证据：已观察事件构成的确定倒序、静态数量矛盾，'
                        '或已有后续动作而必需前置活动始终缺失；单纯的未观察结果记为 open',
            'quantity_policy': case['rules'].get('quantity_policy'),
            'quantity_rules': list(QUANTITY_RULES),
            'parallel': case['rules'].get('parallel'),
            'truncation': '截止时缺少未来动作记为 open，不按偏离计',
        },
    }
