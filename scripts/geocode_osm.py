#!/usr/bin/env python3
"""Reproducible local geocoding from one cached OpenStreetMap extract.

No paid API, no per-address geocoder requests, no municipal-center fallback.
Unambiguous named landmarks and street intersections supply reference anchors.
Nearby amenities are accepted only when type and spatial uniqueness agree.
Otherwise we retain a clearly labelled reference or null, never a precise claim.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://overpass-api.de/api/interpreter'
QUERY = '''[out:json][timeout:90];(
nwr["leisure"](46.115,21.235,46.23,21.405);
nwr["sport"](46.115,21.235,46.23,21.405);
nwr["amenity"~"^(school|college|university|bbq|place_of_worship)$"](46.115,21.235,46.23,21.405);
nwr["addr:housenumber"](46.115,21.235,46.23,21.405);
way["highway"]["name"](46.115,21.235,46.23,21.405);
);out geom;'''


def fold(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold()) if not unicodedata.combining(c))
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    text = re.sub(r'\b(?:dr|doctor|general|gen|prof|profesor|colonel)\b', '', text)
    text = re.sub(r'\bparcul\b|\bparculet\b', 'parc', text)
    # Visible, reviewed spelling/abbreviation expansions. These are matching
    # rules, not edits to the official location descriptions.
    for old, new in [
        ('alexandru t stamatiad', 'alexandru teodor stamatiad'),
        ('alexandru theodor stamatiad', 'alexandru teodor stamatiad'),
        ('dragalina', 'ion dragalina'),
        ('ion ion dragalina', 'ion dragalina'),
        ('praporgescu', 'david praporgescu'),
        ('david david praporgescu', 'david praporgescu'),
        ('p ta', 'piata'),
        ('aleea romanta', 'aleea romantei'),
        ('aleea denza', 'aleea dezna'),
        ('strada george byron', 'strada byron'),
        ('strada brezoianu', 'strada ioan brezoianu'),
        ('ilarion felea', 'felea ilarion'),
        ('ion budai deleanu', 'ioan budai deleanu'),
    ]:
        text = re.sub(r'\b' + old + r'\b', new, text)
    text = re.sub(r'\bbogdan voievod\b(?!\s+i\b)', 'bogdan voievod i', text)
    return re.sub(r'\s+', ' ', text).strip()


def road_key(name):
    return re.sub(r'^(strada|calea|bulevardul|splaiul|splai|aleea|piata)\s+', '', fold(name))


def identifier(element):
    return f"{element['type']}/{element['id']}"


def paths(element):
    if element.get('geometry'):
        return [[[p['lon'], p['lat']] for p in element['geometry'] if 'lat' in p and 'lon' in p]]
    return [[[p['lon'], p['lat']] for p in member['geometry'] if 'lat' in p and 'lon' in p]
            for member in element.get('members', []) if member.get('geometry') and member.get('role') != 'inner']


def center(element):
    if 'lat' in element:
        return [element['lon'], element['lat']]
    geometry = paths(element)
    if not geometry:
        return None
    points = max(geometry, key=len)
    if len(points) > 3 and points[0] == points[-1]:
        cross = [a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:])]
        area = sum(cross)
        if abs(area) > 1e-12:
            return [sum((a[k] + b[k]) * c for a, b, c in zip(points, points[1:], cross)) / (3 * area) for k in (0, 1)]
    return points[len(points) // 2]


def distance(a, b):
    x = (a[0] - b[0]) * 111_320 * math.cos(math.radians((a[1] + b[1]) / 2))
    y = (a[1] - b[1]) * 111_320
    return math.hypot(x, y)


def point_in_polygon(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def typed_amenity(element, subtype):
    tags = element.get('tags', {})
    leisure, sport = tags.get('leisure', ''), set(tags.get('sport', '').split(';'))
    if subtype == 'playground': return leisure == 'playground'
    if subtype == 'dog_park': return leisure == 'dog_park'
    if subtype == 'table_tennis': return 'table_tennis' in sport
    if subtype == 'chess': return 'chess' in sport
    if subtype == 'fitness': return leisure == 'fitness_station'
    if subtype == 'bbq': return tags.get('amenity') == 'bbq'
    if subtype == 'adventure_equipment': return leisure == 'playground' and tags.get('playground') in ('zipwire', 'hammock')
    if subtype == 'multisport': return leisure == 'pitch' and ('multi' in sport or len(sport - {''}) > 1)
    if subtype.startswith('football_'):
        if leisure != 'pitch' or 'soccer' not in sport: return False
        if subtype == 'football_sand': return tags.get('surface') == 'sand'
        return tags.get('surface') in ('tartan', 'rubber', 'rubbercrumb', 'synthetic', 'artificial_turf')
    return False


def reference(point, precision, objects, method, note, confidence='low'):
    refs = sorted({identifier(e) for e in objects})
    return {
        'latitude': round(point[1], 7) if point else None,
        'longitude': round(point[0], 7) if point else None,
        'location_precision': precision, 'geocoding_method': method,
        'geocoding_confidence': confidence,
        'geocoding_reference_ids': refs,
        'geocoding_source_url': 'https://www.openstreetmap.org/' + refs[0] if refs else None,
        'geocoding_note': note,
        'geocoding_anchor_distance_m': None,
    }


def street_matches(text, roads):
    normalized = fold(text)
    found = []
    for key, elements in roads.items():
        if len(key) < 3: continue
        for match in re.finditer(r'\b' + re.escape(key) + r'\b', normalized):
            before = normalized[:match.start()]
            # Require an actual address prefix. A school called Caius Iacob
            # must not accidentally match Piața Caius Iacob elsewhere in town.
            if re.search(r'\b(strada|calea|bulevardul|splaiul|splai|aleea|piata|intersectie cu)\s*$', before):
                found.append((match.start(), -len(key), key, elements))
                break
    result, used = [], set()
    for position, _, key, elements in sorted(found):
        if position in used: continue
        used.add(position)
        result.append((key, elements))
    return result


def intersection(first, second):
    a, b = first[1], second[1]
    first_nodes, second_nodes = {}, {}
    for elements, target in [(a, first_nodes), (b, second_nodes)]:
        for element in elements:
            for nid, p in zip(element.get('nodes', []), element.get('geometry', [])):
                target[nid] = [p['lon'], p['lat']]
    shared = sorted(first_nodes.keys() & second_nodes.keys())
    if shared:
        coords = [first_nodes[n] for n in shared]
        if max(distance(coords[0], p) for p in coords) > 180:
            return None  # streets meeting more than once need disambiguation
        point = [sum(p[k] for p in coords) / len(coords) for k in (0, 1)]
        objects = [e for e in a + b if set(e.get('nodes', [])) & set(shared)]
        return point, objects
    # Divided roads and squares can have separated mapped carriageways. Only
    # accept a nearby endpoint/vertex pair, never an arbitrary distant crossing.
    coords_a, coords_b = list(first_nodes.values()), list(second_nodes.values())
    if not coords_a or not coords_b: return None
    best = min((distance(p, q), p, q) for p in coords_a for q in coords_b)
    if best[0] > 65: return None
    return [(best[1][k] + best[2][k]) / 2 for k in (0, 1)], a + b


def named_landmarks(text, landmarks, street_anchor):
    normalized = fold(text)
    matches = []
    for element in landmarks:
        tags = element.get('tags', {})
        names = [tags.get(key, '') for key in ('name', 'name:ro', 'alt_name', 'official_name')]
        for name in names:
            key = fold(name)
            if len(key) < 7 or len(key.split()) < 2: continue
            if re.search(r'\b' + re.escape(key) + r'\b', normalized):
                point = center(element)
                if street_anchor and distance(point, street_anchor[0]) > 400: continue
                matches.append((len(key), element))
                break
    if not matches: return None
    longest = max(x[0] for x in matches)
    matches = [e for length, e in matches if length == longest]
    if len(matches) > 1 and max(distance(center(matches[0]), center(e)) for e in matches) > 150:
        return None
    # Prefer the outline of a named site to a duplicate name-only node.
    return sorted(matches, key=lambda e: (e['type'] == 'node', identifier(e)))[0]


def block_codes(text):
    # Match block identifiers, never confuse Blocul 155 with street no. 155.
    ascii_text = ''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold()) if not unicodedata.combining(c))
    pattern = r'\b(?:bloc(?:ului|urile|ul)?|bl\.?)\s*(?:nr\.?\s*)?([a-z]*\s*\d+(?:-\d+)?[a-z]?|[a-z]+)\b'
    result = []
    for match in re.finditer(pattern, ascii_text):
        code = re.sub(r'\s+', '', match.group(1))
        staircase = re.match(r'\s*(?:scara|sc\.?)\s*([a-z0-9]+)', ascii_text[match.end():])
        result.append((code, staircase.group(1) if staircase else None))
    return result


def building_anchor(text, addresses):
    blocks = block_codes(text)
    if not blocks: return None
    hits = street_matches(text, addresses)
    if not hits: return None
    matching_streets = [key for key, _ in hits]
    selected = []
    for code, staircase in blocks:
        candidates = []
        for key in matching_streets:
            for element in addresses[key]:
                tags = element.get('tags', {})
                if not tags.get('building'): continue
                labels = tags.get('addr:housenumber', '') + ' ' + tags.get('name', '') + ' ' + tags.get('addr:housename', '')
                for candidate_code, candidate_stair in block_codes(labels):
                    if code == candidate_code:
                        candidates.append((staircase is not None and staircase == candidate_stair, element))
                        break
        if not candidates: continue
        if any(exact for exact, _ in candidates):
            candidates = [(exact, e) for exact, e in candidates if exact]
        objects = list({identifier(e): e for _, e in candidates}.values())
        centers = [center(e) for e in objects]
        if max(distance(centers[0], p) for p in centers) > 180: continue
        selected.extend(objects)
    objects = list({identifier(e): e for e in selected}.values())
    if not objects: return None
    centers = [center(e) for e in objects]
    if max(distance(centers[0], p) for p in centers) > 500: return None
    return [sum(p[k] for p in centers) / len(centers) for k in (0, 1)], objects


def geocode_records(records, root=ROOT):
    raw = root / 'raw'
    payload = gzip.decompress((raw / 'osm-reference.json.gz').read_bytes())
    meta = json.loads((raw / 'osm-source.json').read_text())
    if hashlib.sha256(payload).hexdigest() != meta['sha256']:
        raise ValueError('OpenStreetMap snapshot checksum mismatch')
    osm = json.loads(payload)
    elements = [e for e in osm['elements'] if center(e) is not None]
    roads, addresses, landmarks, amenities = defaultdict(list), defaultdict(list), [], []
    for e in elements:
        tags = e.get('tags', {})
        if tags.get('highway') and tags.get('name'):
            for name in [tags['name']] + tags.get('alt_name', '').split(';'):
                if name: roads[road_key(name)].append(e)
        if tags.get('addr:street') and tags.get('building'):
            addresses[road_key(tags['addr:street'])].append(e)
        if tags.get('leisure') in ('park', 'garden', 'playground') or tags.get('amenity') in ('school', 'college', 'university', 'place_of_worship') or tags.get('building') == 'school':
            if tags.get('name'): landmarks.append(e)
        if tags.get('leisure') or tags.get('sport') or tags.get('amenity') == 'bbq':
            amenities.append(e)
    duplicate = Counter((r['category'], r['subtype'], fold(r['address'])) for r in records)
    fallbacks = {}
    for record in records:
        # A relocation's former address cannot be used as today's anchor.
        text = re.split(r'(?i)\(?relocat de pe', record['address'])[0].strip()
        road_hits = street_matches(text, roads)
        crossing = intersection(*road_hits[:2]) if len(road_hits) >= 2 else None
        landmark = named_landmarks(text, landmarks, crossing)
        building = building_anchor(text, addresses)
        anchor, precision, anchor_objects = None, 'unknown', []
        result = None
        if landmark:
            anchor, anchor_objects = center(landmark), [landmark]
            tags = landmark.get('tags', {})
            if tags.get('leisure') in ('park', 'garden', 'playground'):
                precision = 'park_reference'
                explanation = 'Reper calculat din parcul numit în descriere, pe baza geometriei OSM; poziția dotării în interior nu este stabilită.'
            else:
                precision = 'school_reference' if tags.get('amenity') in ('school', 'college', 'university') or tags.get('building') == 'school' else 'building_reference'
                explanation = 'Reper al instituției/clădirii numite în descriere, identificată în OSM; nu este poziția echipamentului sau intrarea.'
            result = reference(anchor, precision, anchor_objects, 'osm_named_landmark', explanation, 'medium')
        elif building:
            anchor, anchor_objects = building
            precision = 'building_reference'
            result = reference(anchor, precision, anchor_objects, 'osm_block_identifier_and_street', 'Reper calculat din clădirea/clădirile OSM care au codul de bloc și strada din descriere. Nu stabilește poziția dotării aflate în față, în spate sau între blocuri.', 'medium')
        elif crossing:
            anchor, anchor_objects = crossing
            precision = 'intersection_reference'
            result = reference(anchor, precision, anchor_objects, 'osm_named_streets_intersection', 'Reper aproximativ al intersecției din descriere, calculat din noduri OSM comune sau apropiate. Nu indică poziția dotării.', 'medium')
        elif road_hits:
            key, ways = road_hits[0]
            all_points = [p for e in ways for line in paths(e) for p in line]
            # Select a real OSM vertex nearest to the mean of this named road,
            # and explicitly retain the entire-road reference classification.
            mean = [sum(p[k] for p in all_points) / len(all_points) for k in (0, 1)]
            anchor = min(all_points, key=lambda p: distance(p, mean))
            anchor_objects, precision = ways, 'street_reference'
            result = reference(anchor, precision, ways, 'osm_named_street_reference', 'Nod OSM reprezentativ al străzii numite în descriere. Blocul sau echipamentul nu a fost identificat; reperul poate fi departe de amplasamentul exact. Nu folosi pentru navigație la echipament.')
        if result is None:
            result = reference(None, 'unknown', [], 'unresolved', 'Niciun obiect sau reper OSM nu a putut fi asociat fără a ghici amplasamentul.', 'unresolved')
        fallbacks[record['id']] = dict(result)
        # A reference to an entire street is too broad to infer an amenity from
        # its arbitrary midpoint. Limit amenity matching to specific anchors.
        if anchor and precision != 'street_reference' and duplicate[(record['category'], record['subtype'], fold(record['address']))] == 1:
            candidates = []
            for e in amenities:
                if not typed_amenity(e, record['subtype']): continue
                point = center(e)
                d = distance(anchor, point)
                within_site = landmark is not None and any(len(ring) > 3 and ring[0] == ring[-1] and point_in_polygon(point, ring) for ring in paths(landmark))
                if d <= 110 or (within_site and d <= 350):
                    candidates.append((d, identifier(e), e, point))
            candidates.sort(key=lambda x: (x[0], x[1]))
            if candidates:
                first = candidates[0]
                unambiguous = len(candidates) == 1 or (candidates[1][0] - first[0] >= 40 and candidates[1][0] > 2 * first[0] + 20)
                if unambiguous:
                    d, _, e, point = first
                    point_precision = 'osm_amenity_point' if e['type'] == 'node' else 'osm_amenity_center'
                    result = reference(point, point_precision, [e] + anchor_objects, 'osm_typed_amenity_near_text_anchor', 'Potrivire dedusă: obiect OSM de același tip, asociat cu reperul din descriere. Coordonată OSM sau centru calculat al obiectului; identitatea nu a fost verificată pe teren.', 'medium')
                    result['geocoding_anchor_distance_m'] = round(d, 1)
                    result['geocoding_source_url'] = 'https://www.openstreetmap.org/' + identifier(e)
        record.update(result)
    # A shared candidate cannot establish the identity of two distinct rows.
    # Restore their independently established text anchors rather than silently
    # assigning the same equipment to both source entries.
    assigned = defaultdict(list)
    for record in records:
        if record['geocoding_method'] == 'osm_typed_amenity_near_text_anchor':
            assigned[(record['category'], record['geocoding_source_url'])].append(record)
    for same_object in assigned.values():
        if len(same_object) > 1:
            for record in same_object:
                record.update(fallbacks[record['id']])
                record['geocoding_note'] += ' Un obiect OSM apropiat era candidat pentru mai multe poziții; s-a păstrat reperul descrierii, fără a atribui arbitrar același echipament.'
    return records, {**meta, 'osm_element_count': len(osm['elements']), 'method': 'Local deterministic matching against cached OSM features; no public per-address geocoder.', 'matching_confidence_is_not_survey_accuracy': True}


def refresh(root=ROOT):
    request = Request(ENDPOINT + '?' + urlencode({'data': QUERY}), headers={'User-Agent': 'AradRecreationData/1.0 (https://github.com/baditaflorin/arad-recreation-data)'})
    payload = urlopen(request, timeout=110).read()
    osm = json.loads(payload)
    if osm.get('remark') or not osm.get('elements'):
        raise ValueError(osm.get('remark', 'Empty Overpass response; last-known-good snapshot retained'))
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    (root / 'raw/osm-reference.json.gz').write_bytes(compressed)
    (root / 'raw/osm-query.txt').write_text(QUERY + '\n')
    meta = {'endpoint': ENDPOINT, 'query': QUERY, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'timestamp_osm_base': osm.get('osm3s', {}).get('timestamp_osm_base'), 'sha256': hashlib.sha256(payload).hexdigest(), 'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors'}
    (root / 'raw/osm-source.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'osm_elements': len(osm['elements']), 'compressed_bytes': len(compressed)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='Refresh the single Overpass snapshot, then rebuild exports')
    args = parser.parse_args()
    if args.refresh: refresh()
    from scrape_recons import build
    build()
