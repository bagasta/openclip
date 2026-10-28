from pathlib import Path
import json
import math
import tempfile
import unittest
from unittest.mock import patch
from openclip import router


class RouterTests(unittest.TestCase):
    def test_word_timestamps_are_offset_and_plain_text_is_rejected(self):
        response = {'text':'Halo AI','words':[{'word':'Halo','start':1,'end':1.3},{'word':'AI','start':1.3,'end':1.7}]}
        segments = router.normalize_transcript(response,180,10)
        self.assertEqual(segments[0]['start'],181)
        self.assertEqual(segments[-1]['words'][-1]['end'],181.7)
        with self.assertRaises(ValueError):
            router.normalize_transcript({'text':'Halo tanpa waktu'},0,10)

    def test_usage_records_actual_cost_above_previous_budget_without_a_cap(self):
        recorded = []
        usage = router.Usage(.09, recorded.append)
        usage.charge({'usage': {'cost': 1.2}}, .005)
        self.assertAlmostEqual(usage.spent, 1.29)
        self.assertEqual(recorded, [1.29])
        usage.charge({}, .02)
        self.assertAlmostEqual(usage.spent, 1.31)
        with self.assertRaises(ValueError):
            router.Usage(math.nan)

    def test_settings_never_return_key_and_file_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'key.json'
            with patch.object(router,'SETTINGS',target),patch.dict('os.environ',{'OPENROUTER_API_KEY':''}):
                result=router.save_settings('test-key-never-return')
                self.assertTrue(result['key_set'])
                self.assertNotIn('test-key-never-return',json.dumps(result))
                self.assertEqual(target.stat().st_mode&0o777,0o600)
                router.save_settings('')
                self.assertEqual(router.settings()['api_key'],'test-key-never-return')


if __name__=='__main__':
    unittest.main()
