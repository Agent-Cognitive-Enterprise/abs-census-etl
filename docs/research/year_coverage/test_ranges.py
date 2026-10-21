"""Offline tests for seeded range replay; no retained ABS bytes or network."""
import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_ranges import EvidenceRemote, compare_replay


class Provider(io.BytesIO):
    def __init__(self, body, *, size=None):
        super().__init__(body)
        self.size = len(body) if size is None else size
        self.available = len(body)

    def read(self, count=-1):
        if count < 0 or self.tell()+count > self.available:
            raise ValueError('Unrecorded provider bytes')
        return super().read(count)


class HTTP:
    def __init__(self, body, *, size_delta=0):
        self.body, self.calls, self.delta = body, [], size_delta

    def get(self, url, *, start, end):
        self.calls.append((url, start, end))
        return {'Content-Range': f'bytes {start}-{end}/{len(self.body)+self.delta}'}, self.body[start:end+1]


class RangeTests(unittest.TestCase):
    def test_complete_saved_bytes_never_use_network(self):
        body = b'01234567'*2500
        http = HTTP(body)
        with io.BufferedReader(EvidenceRemote([Provider(body)], http=http, url='saved')) as stream:
            self.assertEqual(stream.read(), body)
        self.assertEqual(http.calls, [])

    def test_only_missing_block_is_downloaded_once(self):
        body = b'abcdefgh'*2048
        http = HTTP(body)
        with io.BufferedReader(EvidenceRemote([Provider(body[:8192], size=len(body))],
                                              http=http, url='saved')) as stream:
            self.assertEqual(stream.read(), body)
            stream.seek(0)
            self.assertEqual(stream.read(), body)
        self.assertEqual(http.calls, [('saved', 8192, 16383)])

    def test_offline_gap_raises_instead_of_fabricating_archive_bytes(self):
        with io.BufferedReader(EvidenceRemote([Provider(b'a'*8192, size=16384)], url='saved')) as stream:
            with self.assertRaisesRegex(ValueError, 'Unrecorded source block'):
                stream.read()

    def test_changed_archive_size_is_refused(self):
        body = b'a'*16384
        with io.BufferedReader(EvidenceRemote([Provider(body[:8192], size=len(body))],
                                              http=HTTP(body, size_delta=1), url='saved')) as stream:
            with self.assertRaisesRegex(ValueError, 'Archive changed'):
                stream.read()

    def test_new_runtime_revalidation_still_requires_identical_source_results(self):
        original = {'runtime_sha256': 'old', 'results': [{'population': 3872}]}
        current = {'runtime_sha256': 'new', 'results': [{'population': 3872}]}
        with self.assertRaisesRegex(ValueError, 'Replayed report differs'):
            compare_replay(current, original)
        compare_replay(current, original, allow_new_runtime=True)
        current['results'][0]['population'] = 1
        with self.assertRaisesRegex(ValueError, 'Replayed report differs'):
            compare_replay(current, original, allow_new_runtime=True)
        self.assertEqual(original['runtime_sha256'], 'old')
