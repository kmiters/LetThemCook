import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
from ltc_usage import FIELDS, UsageIndex
from ltc_usage_prices import STANDARD, estimate

STAMP = '2026-10-25T00:15:00Z'
SECRET = 'fixture conversation text must never escape'


def line(kind, p, timestamp=STAMP):
    return {'timestamp': timestamp, 'type': kind, 'payload': p}


def usage(inp=50, read=10, write=2, out=8, reasoning=3):
    return dict(input_tokens=inp, cached_input_tokens=read, cache_write_input_tokens=write,
                output_tokens=out, reasoning_output_tokens=reasoning)


class UsageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.codex = root / 'sessions'
        self.claude = root / 'projects'
        self.codex.mkdir()
        self.claude.mkdir()
        self.index = UsageIndex({'Codex': [self.codex], 'Claude Code': [self.claude]}, ttl=0)
        env = patch.dict(os.environ, TZ='Europe/Warsaw')
        self.addCleanup(time.tzset)
        self.addCleanup(env.stop)
        env.start()
        time.tzset()

    @staticmethod
    def append(path, *events):
        with path.open('a') as f:
            for event in events:
                f.write(json.dumps(event) + '\n')

    def response(self, key, **extra):
        return line('token_usage_record', dict(response_id=key, turn_id='a', usage=usage(), **extra))

    def test_components_dedup_across_copies_turns_and_streamed_blocks(self):
        path = self.codex / '1.jsonl'
        self.append(path, line('session_meta', {'id':'session1'}),
                    line('turn_context', {'turn_id':'a', 'model':'model-a'}),
                    line('turn_context', {'turn_id':'b', 'model':'model-b'}),
                    line('response_item', {'text': SECRET}),
                    self.response('one', thread_token_usage=usage(8000)), self.response('one'),
                    line('event_msg', {'type':'token_count', 'info':{'total_token_usage':usage(),
                                                                    'last_token_usage':usage()}}))
        (self.codex / 'copy.jsonl').write_bytes(path.read_bytes())
        msg = dict(id='one', model='model-c', content=[{'text':SECRET}], usage={
            'input_tokens':7, 'cache_read_input_tokens':100, 'cache_creation_input_tokens':5,
            'output_tokens':8, 'output_tokens_details':{'thinking_tokens':3}})
        self.append(self.claude / 'c.jsonl', {'type':'assistant', 'message':msg, 'timestamp':STAMP})
        msg['usage']['output_tokens'] = 12
        self.append(self.claude / 'c.jsonl', {'type':'assistant', 'message':msg, 'timestamp':STAMP})
        result = self.index.snapshot()
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(SECRET, repr(self.index.files))
        rows = {r['model']: tuple(r[k] for k in FIELDS) for r in result['rows']}
        self.assertEqual(rows, {'model-a':(38,10,2,8,3), 'model-c':(7,100,5,12,3)})

    def test_legacy_fork_baselines_boundary_repeats_and_reset(self):
        def snap(n, last, date):
            def totals(x):
                return usage(x,0,0,x,0)
            return line('event_msg', {'type':'token_count','info':{
                'total_token_usage':totals(n),'last_token_usage':totals(last)}}, date)
        path = self.codex / 'history.jsonl'
        self.append(path, line('session_meta', {'id':'child', 'forked_from_id':'parent'}),
                    snap(1000,300,'2026-10-24T21:50:00Z'),
                    snap(1010,10,'2026-10-24T21:59:59Z'),
                    snap(1010,10,'2026-10-24T22:00:00Z'),
                    snap(1025,15,'2026-10-24T22:00:01Z'),
                    snap(500,50,'2026-10-25T00:15:00Z'),
                    snap(508,8,'2026-10-25T01:15:00Z'))
        rows = self.index.snapshot()['rows']
        self.assertEqual([(r['day'],r['fresh']) for r in rows], [('2026-10-24',10),('2026-10-25',23)])
        self.append(path, snap(512,4,'2026-10-25T23:00:00Z'))
        self.assertEqual(self.index.snapshot()['rows'][-1]['day'], '2026-10-26')

    def test_partial_append_resume_rotation_and_disappearance(self):
        path = self.codex / 'tail.jsonl'
        self.append(path, self.response('a'))
        with path.open('ab') as f:
            partial = json.dumps(self.response('b')).encode()
            f.write(partial[:30])
        self.assertEqual(self.index.snapshot()['rows'][0]['output'], 8)
        with patch('ltc_usage.FileState.parse', side_effect=AssertionError('reread')):
            self.assertEqual(self.index.snapshot()['rows'][0]['output'], 8)
        with path.open('ab') as f:
            f.write(partial[30:] + b'\n{broken\n')
        snapshot = self.index.snapshot()
        self.assertEqual(snapshot['rows'][0]['output'],16)
        self.assertEqual(snapshot['sources']['Codex']['skipped'],1)
        self.assertEqual(snapshot['rows'],UsageIndex(self.index.roots).snapshot()['rows'])
        path.rename(self.codex / 'rotated.jsonl')
        self.append(path, self.response('b'),self.response('c'))
        self.assertEqual(self.index.snapshot()['rows'][0]['output'],24)
        path.write_text('')
        self.assertEqual(self.index.snapshot()['rows'][0]['output'],16)
        (self.codex / 'rotated.jsonl').unlink()
        self.assertEqual(self.index.snapshot()['rows'],[])
        self.claude.rmdir()
        self.assertFalse(self.index.snapshot()['sources']['Claude Code']['available'])

    def test_refresh_cache_and_same_inode_rewrite(self):
        path = self.codex / 'append.jsonl'
        self.append(path, self.response('a'))
        self.index.ttl = 60
        with patch('ltc_usage.time.monotonic', return_value=100):
            first = self.index.snapshot()
            self.append(path, self.response('b'))
            self.assertIs(first, self.index.snapshot())
        with patch('ltc_usage.time.monotonic', return_value=161):
            self.assertEqual(self.index.snapshot()['rows'][0]['output'], 16)
        path.write_text('')
        self.append(path, self.response('c'), self.response('d'), self.response('e'))
        with patch('ltc_usage.time.monotonic', return_value=222):
            self.assertEqual(self.index.snapshot()['rows'][0]['output'], 24)

    def test_matching_snapshot_before_first_structured_response(self):
        snap = line('event_msg', {'type':'token_count','info':{
            'last_token_usage':usage(), 'total_token_usage':usage()}})
        path = self.codex / 'transition.jsonl'
        self.append(path, line('session_meta', {'id':'one'}), snap)
        self.assertEqual(self.index.snapshot()['rows'][0]['output'], 8)
        self.append(path, self.response('a', thread_token_usage=usage()))
        self.assertEqual(self.index.snapshot()['rows'][0]['output'], 8)

    def test_all_gpt_prices_and_thresholds(self):
        # Integer nano-USD per token, independently listed published Standard rates.
        prices = {
            'gpt-6.2-astra': (8000, 400, 10000, 20000, 40000),
            'gpt-6.2-sol': (2000, 100, 2500, 5000, 10000),
            'gpt-6-astra': (10000, 1000, 12500, 25000, 50000),
            'gpt-6.1-sol': (2000, 100, 2500, 5000, 10000),
            'gpt-6.1-terra': (1000, 50, 1250, 2500, 5000),
            'gpt-6-luna': (100, 10, 125, 250, 500),
        }
        self.assertEqual(len(STANDARD), 9)
        for model, (fresh, read, short, long, out) in prices.items():
            with self.subTest(model=model):
                self.assertEqual(estimate(model, (1000,270998,2,10,7), (1,1)),
                                 (1000*fresh,270998*read,short+long,10*out))
                self.assertEqual(estimate(model, (1000,270999,2,10,7), (1,1)),
                                 (2000*fresh,2*270999*read,2*(short+long),15*out))
        self.assertIsNone(estimate('codex-auto-review', (100,100,0,5,2)))
        self.assertIsNone(estimate('Model nieznany', (100,100,0,5,2)))
        self.assertIsNone(estimate('gpt-6.2-sol', (400000,0,0,5,2), single=False))

    def test_haiku_request_boundaries_and_ttl_in_daily_aggregate(self):
        def block(key, read, details):
            return {'type':'assistant', 'timestamp':STAMP, 'message':{
                'id':key, 'model':'claude-haiku-5-5','usage':{'input_tokens':5,
                'cache_read_input_tokens':read, 'cache_creation_input_tokens':10, 'cache_creation':details,
                'output_tokens':20, 'output_tokens_details':{'thinking_tokens':12}}}}
        first = block('first',99985, {'ephemeral_5m_input_tokens':3,'ephemeral_1h_input_tokens':7})
        second = block('second',99986, {})
        self.append(self.claude / 'c.jsonl', first, first, second)
        row = self.index.snapshot()['rows'][0]
        self.assertEqual(row['priced_tokens'],200041)
        self.assertEqual(row['assumed_write_tokens'],10)
        self.assertEqual(row['usd'],dict(fresh=3e-6,read=(99985*10+99986*50)/1e9,
                                         write=(3*125+7*200+10*625)/1e9,output=60000/1e9))
        for model, rates in [('claude-opus-5-5',(4000,200,5000,8000,20000)),
                             ('claude-sonnet-5-5',(2000,100,2500,4000,10000))]:
            f,r,w,l,o = rates
            self.assertEqual(estimate(model,(300000,3,5,7,6),(2,3)),(300000*f,3*r,2*w+3*l,7*o))

    def test_priced_and_unpriced_models_remain_separate(self):
        self.append(self.codex / 'c.jsonl',self.response('a',model='codex-auto-review'),
                    self.response('b',model='gpt-6.2-sol',prompt_cache_retention='12h'))
        rows = {r['model']:r for r in self.index.snapshot()['rows']}
        self.assertIsNone(rows['codex-auto-review']['usd'])
        self.assertEqual(rows['codex-auto-review']['priced_tokens'],0)
        self.assertEqual(rows['gpt-6.2-sol']['usd']['write'],.00001)
        self.assertEqual(rows['gpt-6.2-sol']['assumed_write_tokens'],0)

    def test_spring_dst_and_local_midnight(self):
        events = []
        for i, stamp in enumerate(['2026-03-28T22:59:59Z', '2026-03-28T23:00:00Z',
                                  '2026-03-29T00:59:59Z', '2026-03-29T01:00:00Z',
                                  '2026-03-29T22:00:00Z']):
            event = self.response(str(i))
            event['timestamp'] = stamp
            events.append(event)
        self.append(self.codex / 'dst.jsonl', *events)
        rows = self.index.snapshot()['rows']
        self.assertEqual([(r['day'], r['output']) for r in rows],
                         [('2026-03-28',8),('2026-03-29',24),('2026-03-30',8)])


if __name__ == '__main__':
    unittest.main()
