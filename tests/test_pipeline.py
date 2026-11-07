import csv
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from scrape_recons import CATEGORIES, parse_pdf, build
from geocode_osm import block_codes, street_matches, geocode_records, fold


class DatasetChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = json.loads((ROOT / 'data/records.json').read_text())
        cls.by_id = {r['id']: r for r in cls.records}

    def test_original_pdf_rows_and_sections_are_complete(self):
        source = json.loads((ROOT / 'raw/source.json').read_text())
        parsed, _ = parse_pdf((ROOT / 'raw/recons-hcl453-2026.pdf').read_bytes(), source)
        self.assertEqual(Counter(r['category'] for r in parsed), {'playgrounds': 81, 'leisure': 116, 'sports_fields': 21, 'dog_parks': 4})
        leisure = Counter(r['subtype'] for r in parsed if r['category'] == 'leisure')
        self.assertEqual(leisure, {'table_tennis': 63, 'chess': 47, 'fitness': 3, 'adventure_equipment': 1, 'bbq': 2})
        self.assertEqual([r['id'] for r in parsed], [r['id'] for r in self.records])

    def test_source_duplicates_quantity_and_typos_are_preserved(self):
        self.assertEqual(self.by_id['dog-park-001']['address'], self.by_id['dog-park-002']['address'])
        self.assertNotEqual(self.by_id['dog-park-001']['id'], self.by_id['dog-park-002']['id'])
        self.assertEqual(self.by_id['leisure-005']['quantity_explicit'], 2)
        self.assertEqual(self.by_id['leisure-068']['quantity_explicit'], 4)
        self.assertIn('Denza', self.by_id['playground-19-03']['address'])
        self.assertEqual(self.by_id['leisure-067']['neighborhood'], 'Cartierul Alfa')
        self.assertTrue(self.by_id['leisure-067']['quality_notes'])

    def test_each_category_round_trips_across_three_formats(self):
        combined = set()
        for stem, (_, _, count) in CATEGORIES.items():
            records = json.loads((ROOT / f'data/{stem}.json').read_text())
            with (ROOT / f'data/{stem}.csv').open(newline='') as file:
                rows = list(csv.DictReader(file))
            features = json.loads((ROOT / f'data/{stem}.geojson').read_text())['features']
            self.assertEqual(len(records), count)
            self.assertEqual([r['id'] for r in records], [r['id'] for r in rows])
            self.assertEqual([r['id'] for r in records], [r['id'] for r in features])
            combined.update(r['id'] for r in records)
        self.assertEqual(combined, set(self.by_id))

    def test_points_and_provenance_are_consistent(self):
        features = json.loads((ROOT / 'data/records.geojson').read_text())['features']
        for feature in features:
            r = self.by_id[feature['id']]
            self.assertEqual(feature['properties'], r)
            self.assertIn(r['source_page'], range(96, 105))
            self.assertEqual(r['inventory_date'], '2026-06-08')
            if r['latitude'] is None:
                self.assertIsNone(r['longitude'])
                self.assertIsNone(feature['geometry'])
                self.assertEqual(r['location_precision'], 'unknown')
            else:
                self.assertTrue(46.115 < r['latitude'] < 46.23)
                self.assertTrue(21.235 < r['longitude'] < 21.405)
                self.assertEqual(feature['geometry']['coordinates'], [r['longitude'], r['latitude']])
                self.assertTrue(r['geocoding_reference_ids'])
                self.assertTrue(r['geocoding_source_url'].startswith('https://www.openstreetmap.org/'))
                self.assertIn(r['geocoding_confidence'], ('high', 'medium', 'low'))

    def test_block_codes_do_not_confuse_keyword_with_identifier(self):
        self.assertEqual(block_codes('Blocului 199 Scara B și Blocul 196'), [('199', 'b'), ('196', None)])
        self.assertEqual(block_codes('Bl. Z 13 Sc.A'), [('z13', 'a')])
        self.assertEqual(block_codes('Strada Oituz nr. 155'), [])

    def test_school_name_does_not_match_a_distant_square(self):
        roads = {'academician caius iacob': [{'id': 1}]}
        self.assertEqual(street_matches('Strada Nucet, Școala Caius Iacob', roads), [])
        self.assertEqual(len(street_matches('Piața Academician Caius Iacob', roads)), 1)

    def test_unresolvable_description_stays_null(self):
        record = dict(self.records[0], id='unknown-test', address='Amplasament neprecizat fără stradă sau repere')
        result, _ = geocode_records([record], ROOT)
        self.assertIsNone(result[0]['latitude'])
        self.assertEqual(result[0]['geocoding_method'], 'unresolved')

    def test_source_change_is_detected_before_exports_are_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / 'raw', root / 'raw')
            (root / 'raw/recons-hcl453-2026.pdf').write_bytes(b'%PDF-changed')
            (root / 'data').mkdir()
            (root / 'data/records.json').write_text('["last-known-good"]')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                build(root)
            self.assertEqual((root / 'data/records.json').read_text(), '["last-known-good"]')


if __name__ == '__main__':
    unittest.main()
