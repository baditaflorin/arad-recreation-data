#!/usr/bin/env python3
"""Extract the four Recons inventories from the original PDF, then geocode.

Offline reconstruction is the default. --refresh-source downloads and validates
the complete replacement PDF before replacing the checked-in source snapshot.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = 'https://www.primariaarad.ro/dm_arad/2026/hotarari.nsf/68ED6FAFE192EBCAC2258E51002CA3B3/$FILE/453%20H%20din%2010%2008%202026%20Anexa%203%20Caiet%20de%20sarcini.pdf?Open='
INVENTORY_DATE = '2026-06-08'
CATEGORIES = {
    'playgrounds': ('Locuri de joacă pentru copii', range(96, 101), 81),
    'leisure': ('Agrement', range(101, 103), 116),
    'sports_fields': ('Terenuri de sport', range(103, 104), 21),
    'dog_parks': ('Locuri de joacă pentru câini', range(104, 105), 4),
}
LEISURE_TYPES = {
    'I': ('table_tennis', 'Tenis de masă'),
    'II': ('chess', 'Șah'),
    'III': ('fitness', 'Fitness în aer liber'),
    'IV': ('adventure_equipment', 'Echipamente de agrement'),
    'V': ('bbq', 'Grătare'),
}


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def parse_pdf(content, source):
    records, tables = [], {}
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if len(pdf.pages) != 107:
            raise ValueError(f'Expected the reviewed 107-page document; got {len(pdf.pages)}. Review the new edition before changing the parser.')
        for category, (category_label, pages, expected) in CATEGORIES.items():
            section, subtype, subtype_label = '', '', ''
            group = []
            for number in pages:
                page = pdf.pages[number - 1]
                extracted = page.extract_tables()
                if len(extracted) != 1:
                    raise ValueError(f'Expected one inventory table on PDF page {number}; got {len(extracted)}')
                table = extracted[0]
                tables[str(number)] = table
                if 'Nr.' not in (table[0][0] or ''):
                    raise ValueError(f'Inventory header changed on PDF page {number}')
                for row_number, original in enumerate(table, start=1):
                    cells = [clean(cell) for cell in original]
                    if cells[0].startswith('Secțiunea'):
                        section = cells[0]
                        if category == 'leisure':
                            roman = re.match(r'Secțiunea ([IVX]+)\.', section).group(1)
                            subtype, subtype_label = LEISURE_TYPES[roman]
                        continue
                    if not re.fullmatch(r'\d+\.', cells[0]):
                        continue
                    sequence = int(cells[0][:-1])
                    code, section_number, sports_type = None, None, None
                    if category == 'playgrounds':
                        if len(cells) != 5:
                            raise ValueError(f'Invalid playground row: {original}')
                        code = re.sub(r'^cod\s+', '', cells[2])
                        section_number = int(cells[1].rstrip('.'))
                        description, neighborhood = cells[3:5]
                        # Remove only the documented prefix; preserve its exact
                        # full wording separately in location_description.
                        prefix = f'Locul de joacă cod {code} - {neighborhood} - '
                        address = description[len(prefix):] if description.startswith(prefix) else description
                        subtype, subtype_label = 'playground', 'Loc de joacă'
                        identifier = 'playground-' + code
                    elif category == 'leisure':
                        if len(cells) != 4 or not subtype:
                            raise ValueError(f'Invalid leisure row: {original}')
                        description, neighborhood = cells[2:4]
                        section_number = int(cells[1].rstrip('.'))
                        address, identifier = description, f'leisure-{sequence:03d}'
                    elif category == 'sports_fields':
                        if len(cells) != 4:
                            raise ValueError(f'Invalid sports row: {original}')
                        description, neighborhood, sports_type = cells[1:4]
                        address, identifier = description, f'sport-{sequence:03d}'
                        subtype = {'Multifuncțional': 'multisport', 'Fotbal pe nisip': 'football_sand', 'Fotbal pe tartan': 'football_tartan'}[sports_type]
                        subtype_label = sports_type
                    else:
                        if len(cells) != 3:
                            raise ValueError(f'Invalid dog-park row: {original}')
                        description, neighborhood = cells[1:3]
                        address, identifier = description, f'dog-park-{sequence:03d}'
                        subtype, subtype_label = 'dog_park', 'Parc pentru câini'
                    if not description or not neighborhood:
                        raise ValueError(f'Missing inventory location on page {number}: {original}')
                    quantity = re.search(r'(\d+)\s+bucăți', description)
                    warnings = []
                    if identifier == 'leisure-067':
                        warnings.append('Sursa indică Cartierul Alfa, dar descrierea indică relocarea pe Strada Simion Popa, Blocul 146. Cartierul original este păstrat; geocodarea folosește amplasamentul după relocare.')
                    if identifier == 'playground-19-03':
                        warnings.append('Sursa scrie Aleea Denza. Regula de geocodare folosește Aleea Dezna din OSM; asocierea este susținută și de documentația municipală care indică blocul X12 pe Aleea Dezna. Textul oficial este păstrat neschimbat.')
                    access = 'Accesul și starea de funcționare nu au fost verificate.'
                    if re.search(r'(?i)școal|liceul|colegiul|gimnazial', description):
                        access += ' Amplasamentul este descris la/lângă o unitate de învățământ; inventarul nu dovedește acces public permanent.'
                    record = {
                        'id': identifier, 'category': category, 'category_label': category_label,
                        'subtype': subtype, 'subtype_label': subtype_label,
                        'inventory_number': sequence, 'section_number': section_number,
                        'code': code, 'name': f'{subtype_label} {code or sequence:}'.strip(),
                        'address': address, 'location_description': description,
                        'neighborhood': neighborhood, 'sports_type': sports_type,
                        'quantity_explicit': int(quantity.group(1)) if quantity else None,
                        'inventory_date': INVENTORY_DATE, 'source_section': section or category_label,
                        'source_url': SOURCE_URL, 'source_page': number,
                        'source_page_url': SOURCE_URL + f'#page={number}',
                        'source_table_row': row_number, 'source_sha256': source['sha256'],
                        'public_access': 'not_verified', 'access_note': access,
                        'quality_notes': warnings,
                    }
                    group.append(record)
            if len(group) != expected or sorted(r['inventory_number'] for r in group) != list(range(1, expected + 1)):
                raise ValueError(f'{category}: expected every number 1..{expected}; got {len(group)} rows. Existing exports were not replaced.')
            records.extend(group)
    if len({r['id'] for r in records}) != 222:
        raise ValueError('Duplicate or missing record identifiers')
    return records, tables


def write_csv(path, records):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        for record in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, separators=(',', ':')) if isinstance(v, (dict, list)) else v for k, v in record.items()})


def write_exports(root, records, source, osm_meta):
    data = root / 'data'
    data.mkdir(exist_ok=True)
    for stem, selected in [('records', records)] + [(key, [r for r in records if r['category'] == key]) for key in CATEGORIES]:
        dump(data / f'{stem}.json', selected)
        write_csv(data / f'{stem}.csv', selected)
        features = []
        for record in selected:
            geometry = {'type': 'Point', 'coordinates': [record['longitude'], record['latitude']]} if record['latitude'] is not None else None
            features.append({'type': 'Feature', 'id': record['id'], 'geometry': geometry, 'properties': record})
        dump(data / f'{stem}.geojson', {'type': 'FeatureCollection', 'features': features})
    unmatched = [{'id': r['id'], 'address': r['address'], 'reason': r['geocoding_note']} for r in records if r['latitude'] is None]
    dump(data / 'geocoding-unmatched.json', unmatched)
    precision = Counter(r['location_precision'] for r in records)
    metadata = {
        'dataset': root.name, 'title': 'Agrement și joacă în Arad — inventarele Recons',
        'generated_at': source['retrieved_at'], 'source_retrieved_at': source['retrieved_at'],
        'inventory_date': INVENTORY_DATE, 'decision_date': '2026-08-10',
        'record_count': len(records), 'geocoded_count': len(records) - len(unmatched),
        'category_counts': dict(Counter(r['category'] for r in records)),
        'subtype_counts': dict(Counter(r['subtype'] for r in records)),
        'precision_counts': dict(precision), 'source_urls': [SOURCE_URL],
        'source_sha256': source['sha256'], 'source_pdf_pages': 107,
        'scope': '222 de poziții din patru inventare la data de 08.06.2026, anexate documentației HCL 453/10.08.2026. Pozițiile pot avea același amplasament; nu reprezintă 222 de locații distincte.',
        'limitations': [
            'Inventarierea nu confirmă funcționarea, orarul, accesibilitatea, siguranța sau accesul public actual. Unele dotări sunt în/lângă școli.',
            'Nicio coordonată nu provine din PDF. Toate localizările sunt îmbogățiri OpenStreetMap; precizia și obiectele de referință sunt publicate pentru fiecare rând.',
            'GeoJSON conține puncte WGS84. Un reper de parc, școală, clădire, intersecție sau stradă nu reprezintă poziția exactă a echipamentului ori intrarea sa.',
            'Înregistrările repetate sunt păstrate separat. Două locuri pentru câini au aceeași descriere Parcul Strada Mărului; un reper comun de parc nu stabilește amplasamente distincte.',
            'La poziția de agrement 67, sursa spune Cartierul Alfa, deși descrie relocarea pe Strada Simion Popa. Cartierul sursei nu este corectat tacit.',
            'Numărul de rânduri nu este numărul de piese: unele descrieri indică explicit 2 sau 4 bucăți.',
            'Fără potrivire verificabilă, latitude/longitude și geometry rămân null. Nu se folosește centrul orașului ca soluție de rezervă.',
        ],
        'license': 'Codul proiectului: MIT. Nu a fost identificată o licență explicită pentru reutilizarea datelor oficiale. Îmbogățirea OpenStreetMap: ODbL-1.0, © OpenStreetMap contributors. Vezi DATA_LICENSE.md.',
        'geocoding_source': osm_meta,
    }
    dump(data / 'metadata.json', metadata)
    dump(data / 'schema.json', {
        'version': '1.0.0', 'encoding': 'UTF-8', 'crs': 'WGS84 EPSG:4326',
        'granularity': 'One source inventory row; retain separate rows sharing an address or coordinates.',
        'nulls': 'JSON null / empty CSV cell = unknown or not applicable; not zero.',
        'fields': {
            'id': 'Stable category + source code/number; playground codes are preserved.',
            'category': list(CATEGORIES), 'subtype': 'Machine-readable amenity or sports type.',
            'inventory_number': 'Original row number within its inventory.',
            'section_number': 'Original row number within its neighborhood/leisure section, where printed.',
            'code': 'Official source playground code; null in other inventories.',
            'name': 'Generated short label, not an additional official name.',
            'address': 'Source location text with only the repetitive playground prefix removed.',
            'location_description': 'Full original source cell, whitespace normalized.',
            'neighborhood': 'Original source neighborhood, including apparent inconsistencies.',
            'quantity_explicit': 'Integer only when the description explicitly states N bucăți; never inferred from row counts.',
            'inventory_date': 'Date printed on the inventory; not download date.',
            'source_page': '1-based page number in the 107-page PDF, not annex-internal pagination.',
            'source_table_row': '1-based row of the extracted PDF table, including its headers.',
            'source_sha256': 'SHA-256 of the original PDF bytes.',
            'latitude': 'Point latitude, degrees; null when unresolved.',
            'longitude': 'Point longitude, degrees; null when unresolved.',
            'location_precision': 'osm_amenity_point / osm_amenity_center / park_reference / school_reference / building_reference / intersection_reference / street_reference / unknown. All are inferred, not survey coordinates.',
            'geocoding_reference_ids': 'List of OSM type/id identifiers supporting this location.',
            'geocoding_method': 'Deterministic matching method; see scripts/geocode_osm.py.',
            'geocoding_note': 'Human-readable explanation, including why a location is approximate or unresolved.',
            'geocoding_source_url': 'OSM reference object URL, never an invented coordinate source.',
            'geocoding_anchor_distance_m': 'Distance from matched amenity point/center to textual reference anchor, when applicable.',
            'geocoding_confidence': 'Qualitative matching confidence (high/medium/low/unresolved), not a statistical probability.',
            'public_access': 'Always not_verified for this snapshot.',
            'quality_notes': 'Array of source issues; JSON-encoded in CSV.',
        },
    })
    print(json.dumps({'records': len(records), 'with_coordinates': len(records) - len(unmatched), 'precision': precision}, ensure_ascii=False))


def build(root=ROOT, refresh_source=False):
    raw = root / 'raw'
    path, manifest = raw / 'recons-hcl453-2026.pdf', raw / 'source.json'
    if refresh_source:
        content = urlopen(Request(SOURCE_URL, headers={'User-Agent': 'AradRecreationData/1.0 civic data extraction'}), timeout=60).read()
        source = {'url': SOURCE_URL, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content), 'inventory_date': INVENTORY_DATE, 'decision_date': '2026-08-10'}
    else:
        content, source = path.read_bytes(), json.loads(manifest.read_text())
        if hashlib.sha256(content).hexdigest() != source['sha256']:
            raise ValueError('Original PDF checksum mismatch; refusing to overwrite exports')
    records, tables = parse_pdf(content, source)
    from geocode_osm import geocode_records
    records, osm_meta = geocode_records(records, root)
    # All validation and geocoding completes before updating source/exports.
    if refresh_source:
        path.write_bytes(content)
        dump(manifest, source)
    dump(raw / 'extracted-tables.json', tables)
    write_exports(root, records, source, osm_meta)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--offline', action='store_true', help='Rebuild from checked-in snapshots (default)')
    mode.add_argument('--refresh-source', action='store_true', help='Download and validate the current PDF')
    build(refresh_source=parser.parse_args().refresh_source)
