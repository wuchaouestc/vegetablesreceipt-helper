import copy
from pathlib import Path
import tempfile
import unittest
from handwriting.acceptance import acceptance_report
from handwriting.storage import digest, fingerprint


class HumanAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.image=self.root/'image.png'
        self.image.write_bytes(b'fixed-test-input')
        self.predictions=[{'category':'生姜','customer_code':'001','weight':'2.50','unit':'斤'},
                          {'category':'生姜','customer_code':'001','weight':'3','unit':'斤'}]
        self.results=[{'request':{'image_sha256':digest(self.image),'provider':{'model':'deepseek-chat'}},
                       'response':{'status':'ok','records':self.predictions}}]
        self.reference={'schema_version':1,'human_attestation':True,'reviewer':'TEST_FIXTURE_NOT_REAL_REVIEW',
            'confirmed_at':'2026-11-06','results_fingerprint':fingerprint(self.results),'annotations':[
                {'image_sha256':digest(self.image),'image_path':'image.png','review_status':'confirmed','complete':True,
                 'records':[dict(self.predictions[0],row_id='row1',weight='2.5',prediction_index=0),
                            dict(self.predictions[1],row_id='row2',prediction_index=1)]}]}

    def tearDown(self):self.temp.cleanup()

    def test_known_fixture_scores_without_touching_response(self):
        original=copy.deepcopy(self.results)
        report=acceptance_report(self.reference,self.results,self.root)
        scores=report['images'][0]['scores']
        self.assertEqual(scores['complete_row_accuracy'],1)
        self.assertNotIn('unflagged_errors',scores)
        self.assertEqual(self.results,original)

    def test_ai_draft_missing_attestation_or_changed_response_rejected(self):
        for key,value in [('human_attestation',False),('reviewer',''),('results_fingerprint','changed')]:
            ref=copy.deepcopy(self.reference);ref[key]=value
            with self.assertRaises(ValueError):acceptance_report(ref,self.results,self.root)
        ref=copy.deepcopy(self.reference);ref['annotations'][0]['review_status']='draft_visual'
        with self.assertRaises(ValueError):acceptance_report(ref,self.results,self.root)

    def test_duplicate_mapping_and_unknown_truth_rejected(self):
        ref=copy.deepcopy(self.reference);ref['annotations'][0]['records'][1]['prediction_index']=0
        with self.assertRaises(ValueError):acceptance_report(ref,self.results,self.root)
        ref=copy.deepcopy(self.reference);ref['annotations'][0]['records'][0]['category']=None
        with self.assertRaises(ValueError):acceptance_report(ref,self.results,self.root)

    def test_missing_prediction_and_wrong_code_are_real_errors(self):
        ref=copy.deepcopy(self.reference)
        ref['annotations'][0]['records'][0]['customer_code']='1'
        ref['annotations'][0]['records'][1]['prediction_index']=None
        scores=acceptance_report(ref,self.results,self.root)['images'][0]['scores']
        self.assertEqual(scores['recall'],.5)
        self.assertEqual(scores['complete_row_accuracy'],0)
        self.assertEqual(scores['field_correct']['customer_code'],0)

    def test_image_changed_rejected(self):
        self.image.write_bytes(b'different')
        with self.assertRaises(ValueError):acceptance_report(self.reference,self.results,self.root)


if __name__=='__main__':unittest.main()
