from collections import defaultdict
from app.rules import split_balance
from app.store import unique


def object_events(case, cutoff):
    unique(case['events'], 'event_id')
    indexed = defaultdict(list)
    for event in case['events']:
        if event['received_at'] > cutoff:
            continue
        if event['occurred'][0] > event['occurred'][1]:
            raise ValueError('reversed occurrence interval')
        for identifier in event['objects']:
            indexed[identifier].append(event)
    return dict(indexed)


def related_objects(case, order):
    found = {order}
    changed = True
    while changed:
        changed = False
        for link in case['links']:
            if link['kind'] in ('split', 'replacement') and link['source'] in found and link['target'] not in found:
                found.add(link['target'])
                changed = True
    trucks = {link['target'] for link in case['links'] if link['kind'] == 'truck' and link['source'] in found}
    return found | trucks


def timeline(case, order, cutoff):
    objects = related_objects(case, order)
    index = object_events(case, cutoff)
    events = {event['event_id']: event for identifier in objects for event in index.get(identifier, [])}
    return sorted(events.values(), key=lambda event: (event['occurred'][0], event['event_id']))


def preview(case):
    return {'snapshot': case['_snapshot'], 'split_balance': split_balance(case),
            'timelines': {order['id']: timeline(case, order['id'], case['cutoff']) for order in case['orders']},
            'display_order_is_not_causality': True}
