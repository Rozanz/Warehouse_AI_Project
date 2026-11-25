"""Extra tests for the final candidate, including mocked model integration (not live AI)."""
import unittest, tempfile, os, sys, types
from pathlib import Path
from unittest.mock import patch
import engine as e
from retrieval import retrieve, for_intent
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.previous=e.DB;e.DB=Path(self.tmp.name)/'test.db';e.init()
    def tearDown(self):e.DB=self.previous;self.tmp.cleanup()
    def request(self,msg,pred='CANCEL',status='ok'):
        fake=types.SimpleNamespace(analyze=lambda m,o:dict(intent=pred,model_status=status,tokens=42,policy='mock'))
        with patch.dict(sys.modules,{'orchestrator':fake}),patch.dict(os.environ,{'WAREHOUSE_AI':'1'}):return e.request('cust_402','ORD-99182',msg)
    def test_indirect_cancellation(self):
        r=self.request("I changed my mind about buying these headphones. I don't need them anymore.")
        t=e.rows('SELECT * FROM tickets WHERE id=?',(r['ticket'],))[0]
        self.assertEqual(t['intent'],'CANCEL');self.assertIn('P01',t['policy']);self.assertTrue(t['customer_request']);self.assertFalse(e.ship('ORD-99182'));self.assertEqual(e.decide(t['id'],True),'APPROVED')
    def test_conditional_guard(self):
        r=self.request('Cancel if unshipped otherwise update address');t=e.rows('SELECT * FROM tickets WHERE id=?',(r['ticket'],))[0]
        self.assertEqual(t['intent'],'REVIEW');self.assertIn('P07',t['policy']);self.assertEqual(e.decide(t['id'],True),'MANUAL_FOLLOWUP');self.assertEqual(e.rows('SELECT status FROM orders WHERE id=?',('ORD-99182',))[0]['status'],'PROCESSING')
    def test_model_failure(self):
        r=self.request('cancel order','REVIEW','unavailable or invalid; safe review');self.assertEqual(e.rows('SELECT intent FROM tickets WHERE id=?',(r['ticket'],))[0]['intent'],'REVIEW')
    def test_no_sensitive_downgrade(self):
        r=self.request('cancel order','STATUS');self.assertEqual(e.rows('SELECT intent FROM tickets WHERE id=?',(r['ticket'],))[0]['intent'],'REVIEW')
    def test_boolean_approval(self):
        with patch.dict(os.environ,{'WAREHOUSE_AI':'0'}):r=e.request('cust_402','ORD-99182','cancel')
        with self.assertRaises(ValueError):e.decide(r['ticket'],'false')
    def test_retrieval(self):
        hits,backend=retrieve('I changed my mind and no longer need headphones');self.assertEqual(hits[0]['id'],'P01');self.assertIn('cosine',backend);self.assertEqual(for_intent('REVIEW','cancel otherwise edit')[0]['id'],'P07')
    def test_privacy_and_followup(self):
        r=self.request('please email me at test@example.com','REVIEW');t=e.rows('SELECT * FROM tickets WHERE id=?',(r['ticket'],))[0];self.assertNotIn('test@example.com',t['customer_request']);self.assertEqual(e.decide(t['id'],True),'MANUAL_FOLLOWUP');self.assertEqual(e.close_followup(t['id']),'DISMISSED')
if __name__=='__main__':unittest.main(verbosity=2)
