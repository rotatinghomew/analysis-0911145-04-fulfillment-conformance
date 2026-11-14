import unittest
from app.fulfillment import timeline, related_objects
from app.rules import split_balance, certain_before
from app.store import load_case


class FlowTests(unittest.TestCase):
    def test_split_units_conserve(self):
        self.assertEqual(split_balance(load_case('standard')), {'o1': 0, 'o2': 0})

    def test_shared_truck_does_not_merge_other_order(self):
        objects = related_objects(load_case('standard'), 'o1')
        self.assertIn('t1', objects)
        self.assertIn('p4', objects)
        self.assertNotIn('o2', objects)
        self.assertNotIn('p3', objects)

    def test_overlap_is_not_known_before(self):
        self.assertFalse(certain_before({'occurred': [2, 5]}, {'occurred': [4, 6]}))

    def test_late_received_event_is_absent(self):
        events = timeline(load_case('standard'), 'o1', 14)
        self.assertNotIn('e12', {event['event_id'] for event in events})
