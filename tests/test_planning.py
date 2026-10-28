import unittest
from unittest.mock import patch
import io
import json

from openclip.planning import candidates, parse_srt, select_clips, words_for_clip, rank_ollama
from openclip.media import ass_text


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.segments = []
        for i in range(16):
            text = f'Bagaimana cara membangun bisnis {i}? Ternyata pelanggan membutuhkan solusi yang sederhana.'
            words = text.split()
            self.segments.append({'start': i*6, 'end': i*6+5.5, 'text': text, 'words': [
                {'start': i*6+j*5.5/len(words), 'end': i*6+(j+1)*5.5/len(words), 'word': word}
                for j, word in enumerate(words)]})

    def test_selected_ranges_are_bounded_and_not_duplicates(self):
        chosen = select_clips(candidates(self.segments, 15, 30, 'bisnis pelanggan'), 3)
        self.assertEqual(len(chosen), 3)
        for item in chosen:
            self.assertGreaterEqual(item['end']-item['start'], 15)
            self.assertLessEqual(item['end']-item['start'], 30)
            self.assertLessEqual(item['end'], 96)
        for i, item in enumerate(chosen):
            for other in chosen[i+1:]:
                overlap = max(0, min(item['end'], other['end'])-max(item['start'], other['start']))
                self.assertLessEqual(overlap, .2*min(item['end']-item['start'], other['end']-other['start']))

    def test_srt_import_clamps_to_video_and_strips_markup(self):
        parsed = parse_srt('1\r\n00:00:02,000 --> 00:00:06,000\r\n<b>Hello</b> world\r\n', 5)
        self.assertEqual(parsed[0]['text'], 'Hello world')
        self.assertEqual(parsed[0]['end'], 5)
        self.assertEqual(parsed[0]['words'][0]['start'], 2)
        with self.assertRaises(ValueError):
            parse_srt('not a subtitle file', 60)

    def test_clip_captions_use_relative_times_and_escape_ass_commands(self):
        words = words_for_clip(self.segments, 2, 9)
        self.assertTrue(words)
        self.assertTrue(all(0 <= w['start'] < w['end'] <= 7 for w in words))
        words[0]['word'] = '{\\pos(1,1)}hi'
        output = ass_text(words, 720, 1280)
        self.assertNotIn('\\pos(', output)
        self.assertIn('PlayResY: 1280', output)

    def test_short_speech_is_not_padded_with_invented_content(self):
        with self.assertRaises(ValueError):
            candidates(self.segments[:1], 20, 40)

    def test_llm_instruction_placeholders_are_not_shipped_as_titles(self):
        pool = candidates(self.segments, 15, 30)
        output = {'clips': [{'candidate_id': pool[0]['candidate_id'], 'score': 75,
                            'title': 'short title in source language', 'reason': 'brief editorial reason'}]}
        tags = io.BytesIO(b'{"models":[]}')
        stream = io.BytesIO((json.dumps({'message': {'content': json.dumps(output)}})+'\n').encode())
        with patch('openclip.planning.urllib.request.urlopen', side_effect=[tags, stream]):
            with self.assertRaises(ValueError):
                rank_ollama(pool, 'test-model', '')


if __name__ == '__main__':
    unittest.main()
