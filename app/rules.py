from collections import defaultdict


def split_balance(case):
    totals = defaultdict(int)
    for link in case['links']:
        if link['kind'] == 'split':
            if link['units'] <= 0:
                raise ValueError('split must carry positive units')
            totals[link['source']] += link['units']
    return {order['id']: order['units'] - totals[order['id']] for order in case['orders']}


def certain_before(left, right):
    return left['occurred'][1] < right['occurred'][0]
