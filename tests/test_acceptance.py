import os
import threading
import unittest

from app.client import analysis
from app.server import make_server
from app.store import load_case


@unittest.skipUnless(os.environ.get('RUN_ACCEPTANCE') == '1', 'new analysis feature not enabled')
class AnalysisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server()
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=3)
        cls.server.server_close()

    def test_result_has_consumer_fields_and_provenance(self):
        result = analysis(self.base)
        self.assertEqual(result['snapshot'], load_case('standard')['_snapshot'])
        for field in ['cases', 'patterns', 'shared_events', 'diagnostics']:
            self.assertIn(field, result)
        self.assertTrue(result['diagnostics'])

    def test_edge_case_is_not_silently_treated_as_standard(self):
        standard = analysis(self.base)
        edge = analysis(self.base, 'edge')
        self.assertNotEqual(standard['snapshot'], edge['snapshot'])
        self.assertTrue(edge['diagnostics'])

    def test_file_row_order_does_not_define_event_causality(self):
        original = analysis(self.base, cutoff=20)
        permuted = analysis(self.base, 'permuted', cutoff=20)
        statuses = {row['order']: row['status'] for row in original['cases']}
        self.assertEqual(statuses, {row['order']: row['status'] for row in permuted['cases']})

    def test_observation_ending_before_packing_is_not_a_failure(self):
        result = analysis(self.base, cutoff=2)
        self.assertTrue(result['cases'])
        self.assertTrue(all(row['status'] == 'open' for row in result['cases']))
