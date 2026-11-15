"""符合性分析的算法测试：并发语义、数量守恒、截尾与顺序歧义、聚合与下钻。

测试用例由测试现场构造，不依赖固定 fixture 名称返回预制结论。
"""
import unittest

from app.conformance import conformance
from app.store import load_case

RULES = {
    'allowed': {'order': ['accepted'],
                'parcel': ['packed', 'delivered', 'damaged', 'replacement_authorized'],
                'truck': ['departed']},
    'quantity_policy': 'replacement_consumes_failed_units',
    'required': ['accepted_before_pack', 'pack_before_departure', 'depart_before_delivery'],
    'parallel': 'different_parcels_may_overlap',
}


def event(event_id, activity, objects, earliest, latest, received):
    return {'event_id': event_id, 'activity': activity, 'objects': objects,
            'occurred': [earliest, latest], 'received_at': received}


def make_case(orders, parcels, trucks, links, events, cutoff=100):
    return {'orders': orders, 'parcels': parcels, 'trucks': trucks, 'links': links,
            'events': events, 'cutoff': cutoff, 'rules': RULES, '_snapshot': 'synthetic'}


def simple_case(pack_a=(2, 3), pack_b=(2, 3), merchant='m1'):
    """一单拆两箱、并行打包、同车发运、均签收的基准情形。"""
    orders = [{'id': 'o1', 'units': 4, 'merchant': merchant}]
    parcels = [{'id': 'p1', 'units': 2}, {'id': 'p2', 'units': 2}]
    trucks = [{'id': 't1'}]
    links = [{'kind': 'split', 'source': 'o1', 'target': 'p1', 'units': 2},
             {'kind': 'split', 'source': 'o1', 'target': 'p2', 'units': 2},
             {'kind': 'truck', 'source': 'p1', 'target': 't1'},
             {'kind': 'truck', 'source': 'p2', 'target': 't1'}]
    events = [event('e0', 'accepted', ['o1'], 1, 1, 2),
              event('e1', 'packed', ['p1'], *pack_a, 4),
              event('e2', 'packed', ['p2'], *pack_b, 4),
              event('e3', 'departed', ['t1'], 5, 5, 6),
              event('e4', 'delivered', ['p1'], 7, 7, 8),
              event('e5', 'delivered', ['p2'], 7, 7, 8)]
    return make_case(orders, parcels, trucks, links, events)


def row(result, order='o1'):
    return next(item for item in result['cases'] if item['order'] == order)


def findings(result, order='o1', kind=None, rule=None):
    items = row(result, order)['findings']
    if kind:
        items = [item for item in items if item['kind'] == kind]
    if rule:
        items = [item for item in items if item['rule'] == rule]
    return items


class ConcurrencyTests(unittest.TestCase):
    def test_parallel_packing_of_distinct_parcels_is_conforming(self):
        # 区间相交的并行打包发生在不同包裹上，规则明确允许。
        result = conformance(simple_case())
        self.assertEqual(row(result)['status'], 'conforming')
        self.assertEqual(row(result)['findings'], [])

    def test_overlapping_duplicate_pack_of_same_parcel_is_deviating(self):
        case = simple_case()
        case['events'].append(event('e6', 'packed', ['p1'], 3, 4, 5))
        result = conformance(case)
        hits = findings(result, kind='violation', rule='parcel_lifecycle')
        self.assertEqual(row(result)['status'], 'deviating')
        self.assertEqual(sorted(hits[0]['evidence']), ['e1', 'e6'])


class QuantityTests(unittest.TestCase):
    def test_split_sum_must_equal_order_units(self):
        case = simple_case()
        case['links'][1]['units'] = 1  # 拆出 2+1 != 订单 4
        result = conformance(case)
        self.assertEqual(row(result)['status'], 'deviating')
        self.assertTrue(findings(result, kind='violation', rule='split_units_match_order'))

    def test_replacement_units_must_match_failed_units(self):
        case = simple_case()
        case['parcels'].append({'id': 'p3', 'units': 2})
        case['links'].append({'kind': 'replacement', 'source': 'p2', 'target': 'p3', 'units': 1})
        case['events'] = [e for e in case['events'] if e['event_id'] != 'e5']
        case['events'] += [event('e5', 'damaged', ['p2'], 7, 7, 8),
                           event('e6', 'replacement_authorized', ['p2', 'p3'], 8, 8, 9),
                           event('e7', 'packed', ['p3'], 9, 9, 10),
                           event('e8', 'delivered', ['p3'], 11, 11, 12)]
        result = conformance(case)
        hits = findings(result, kind='violation', rule='replacement_units_match_failed')
        self.assertTrue(hits)
        self.assertIn('e5', hits[0]['evidence'])

    def test_delivered_units_must_close_order(self):
        case = simple_case()
        case['events'] = [e for e in case['events'] if e['event_id'] != 'e5']
        case['events'].append(event('e5', 'damaged', ['p2'], 7, 7, 8))  # 失败 2 件且无补发
        result = conformance(case)
        self.assertEqual(row(result)['status'], 'deviating')
        self.assertTrue(findings(result, kind='violation', rule='delivered_units_match_order'))

    def test_replacement_branch_requires_failed_source(self):
        case = simple_case()
        case['parcels'].append({'id': 'p3', 'units': 2})
        case['links'].append({'kind': 'replacement', 'source': 'p2', 'target': 'p3', 'units': 2})
        case['events'] += [event('e6', 'replacement_authorized', ['p2', 'p3'], 8, 8, 9),
                           event('e7', 'packed', ['p3'], 9, 9, 10)]
        result = conformance(case)  # p2 正常签收却启动了补发
        hits = findings(result, kind='violation', rule='replacement_target_is_failed')
        self.assertTrue(hits)
        self.assertIn('e6', hits[0]['evidence'])

    def test_active_replacement_requires_authorization(self):
        case = simple_case()
        case['parcels'].append({'id': 'p3', 'units': 2})
        case['links'].append({'kind': 'replacement', 'source': 'p2', 'target': 'p3', 'units': 2})
        case['events'] = [e for e in case['events'] if e['event_id'] != 'e5']
        case['events'] += [event('e5', 'damaged', ['p2'], 7, 7, 8),
                           event('e6', 'packed', ['p3'], 9, 9, 10)]
        result = conformance(case)
        self.assertTrue(findings(result, kind='violation', rule='replacement_authorized_for_link'))

    def test_failed_parcel_without_branch_activity_is_open(self):
        case = simple_case()
        case['parcels'].append({'id': 'p3', 'units': 2})
        case['links'].append({'kind': 'replacement', 'source': 'p2', 'target': 'p3', 'units': 2})
        case['events'] = [e for e in case['events'] if e['event_id'] != 'e5']
        case['events'].append(event('e5', 'damaged', ['p2'], 7, 7, 8))
        result = conformance(case, cutoff=8)  # 失败已见，补发分支尚未开始
        self.assertEqual(row(result)['status'], 'open')
        self.assertFalse(findings(result, kind='violation'))


class TruncationAndOrderTests(unittest.TestCase):
    def test_overlapping_intervals_are_indeterminate_not_deviating(self):
        result = conformance(simple_case(pack_a=(2, 5)))  # 打包 [2,5] 与发车 [5,5] 相交
        self.assertEqual(row(result)['status'], 'indeterminate')
        hits = findings(result, kind='indeterminate', rule='pack_before_departure')
        self.assertEqual(sorted(hits[0]['evidence']), ['e1', 'e3'])

    def test_certain_inversion_is_deviating(self):
        case = simple_case()
        case['events'][0] = event('e0', 'accepted', ['o1'], 4, 4, 5)  # 接单确定晚于打包
        result = conformance(case)
        hits = findings(result, kind='violation', rule='accepted_before_pack')
        self.assertEqual(row(result)['status'], 'deviating')
        self.assertIn('e0', hits[0]['evidence'])

    def test_truncation_is_open_not_deviating(self):
        result = conformance(simple_case(), cutoff=3)  # 只观察到接单
        self.assertEqual(row(result)['status'], 'open')
        self.assertFalse(findings(result, kind='violation'))

    def test_in_transit_at_cutoff_is_open(self):
        case = simple_case()
        case['events'] = [e for e in case['events'] if e['event_id'] != 'e5']
        result = conformance(case)  # p2 已发运但无结局
        self.assertEqual(row(result)['status'], 'open')
        self.assertTrue(findings(result, kind='open', rule='awaiting_outcome'))

    def test_cutoff_beyond_horizon_rejected(self):
        with self.assertRaises(ValueError):
            conformance(simple_case(), cutoff=101)

    def test_event_row_order_does_not_change_statuses(self):
        case = simple_case(pack_a=(2, 5))
        reordered = dict(case, events=list(reversed(case['events'])))
        first = {r['order']: r['status'] for r in conformance(case)['cases']}
        second = {r['order']: r['status'] for r in conformance(reordered)['cases']}
        self.assertEqual(first, second)


class AggregationAndDrilldownTests(unittest.TestCase):
    def shared_case(self, merchants=('m1', 'm1')):
        """两单各自一箱，合乘同一辆车；发车事件为公共事件。"""
        orders = [{'id': 'o1', 'units': 2, 'merchant': merchants[0]},
                  {'id': 'o2', 'units': 2, 'merchant': merchants[1]}]
        parcels = [{'id': 'p1', 'units': 2}, {'id': 'p2', 'units': 2}]
        trucks = [{'id': 't1'}]
        links = [{'kind': 'split', 'source': 'o1', 'target': 'p1', 'units': 2},
                 {'kind': 'split', 'source': 'o2', 'target': 'p2', 'units': 2},
                 {'kind': 'truck', 'source': 'p1', 'target': 't1'},
                 {'kind': 'truck', 'source': 'p2', 'target': 't1'}]
        events = [event('e0', 'accepted', ['o1'], 1, 1, 2),
                  event('e1', 'accepted', ['o2'], 1, 1, 2),
                  event('e2', 'packed', ['p1'], 2, 3, 4),
                  event('e3', 'packed', ['p2'], 2, 3, 4),
                  event('e4', 'departed', ['t1'], 5, 5, 6),
                  event('e5', 'delivered', ['p1'], 7, 7, 8),
                  event('e6', 'delivered', ['p2'], 7, 7, 8)]
        return make_case(orders, parcels, trucks, links, events)

    def test_shared_truck_event_kept_once_with_all_orders(self):
        result = conformance(self.shared_case())
        self.assertEqual([item['event_id'] for item in result['shared_events']], ['e4'])
        self.assertEqual(result['shared_events'][0]['orders'], ['o1', 'o2'])

    def test_shared_event_not_counted_per_order_in_merchant_patterns(self):
        result = conformance(self.shared_case(merchants=('m1', 'm1')))
        self.assertEqual(result['patterns']['m1']['shared_events_unique'], 1)
        split = conformance(self.shared_case(merchants=('m1', 'm2')))
        self.assertEqual(split['patterns']['m1']['shared_events_unique'], 1)
        self.assertEqual(split['patterns']['m2']['shared_events_unique'], 1)

    def test_merchant_patterns_aggregate_deviations_and_rework(self):
        result = conformance(load_case('standard'))
        self.assertEqual(set(result['patterns']), {'m1', 'm2'})
        self.assertEqual(result['patterns']['m1']['rework_parcels'], 1)
        self.assertEqual(result['patterns']['m2']['rework_parcels'], 0)
        for bucket in result['patterns'].values():
            total = sum(bucket['status_counts'].values())
            self.assertEqual(total, bucket['orders'])
            self.assertIn('unit', bucket)

    def test_evidence_ids_resolve_to_real_events(self):
        for name in ('standard', 'edge'):
            case = load_case(name)
            known = {e['event_id'] for e in case['events']}
            result = conformance(case)
            for row_ in result['cases']:
                for finding in row_['findings']:
                    self.assertLessEqual(set(finding['evidence']), known)
                self.assertLessEqual(set(row_['evidence']), known)
            self.assertLessEqual({s['event_id'] for s in result['shared_events']}, known)

    def test_diagnostics_explain_indeterminate_branches(self):
        result = conformance(load_case('edge'))
        self.assertTrue(result['diagnostics'])
        for item in result['diagnostics']:
            self.assertIn('rule', item)
            self.assertIn('detail', item)


if __name__ == '__main__':
    unittest.main()
