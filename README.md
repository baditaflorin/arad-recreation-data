# Joacă și agrement în Arad — inventarele Recons

[Portal public: descărcări, hartă și exemple](https://baditaflorin.github.io/arad-recreation-data/)

**222 de poziții din patru inventare**, datate 8 iunie 2026, publicate în
anexele documentației HCL 453/10.08.2026. Nu sunt 222 de locații distincte:
același amplasament poate conține mai multe dotări sau mai multe rânduri.
Inventarul nu confirmă funcționarea și accesul public actual.

## Descărcări gata de folosit

- Complet: [CSV](data/records.csv) · [JSON](data/records.json) · [GeoJSON](data/records.geojson).
- [Metadate](data/metadata.json) · [Dicționarul datelor](data/schema.json) · [Cazuri fără localizare](data/geocoding-unmatched.json).
- [PDF original](raw/recons-hcl453-2026.pdf) · [Tabele extrase, cu celulele originale](raw/extracted-tables.json).
- [ZIP cu codul, exemplele, sursele și datele](https://baditaflorin.github.io/arad-recreation-data/downloads/source.zip).

| Inventar | Poziții | Cu coordonate / reper | Descărcări |
| --- | ---: | ---: | --- |
| Locuri de joacă | 81 | 80 | [CSV](data/playgrounds.csv) · [JSON](data/playgrounds.json) · [GEOJSON](data/playgrounds.geojson) |
| Agrement | 116 | 115 | [CSV](data/leisure.csv) · [JSON](data/leisure.json) · [GEOJSON](data/leisure.geojson) |
| Terenuri de sport | 21 | 21 | [CSV](data/sports_fields.csv) · [JSON](data/sports_fields.json) · [GEOJSON](data/sports_fields.geojson) |
| Parcuri pentru câini | 4 | 4 | [CSV](data/dog_parks.csv) · [JSON](data/dog_parks.json) · [GEOJSON](data/dog_parks.geojson) |

Agrementul păstrează subtipurile: **63** poziții de tenis de masă, **47** de șah,
**3** de fitness, **1** de echipamente de agrement (hamace/tiroliene etc.) și
**2** de grătare. Un rând poate indica explicit 2 sau 4 bucăți; numărul de
înregistrări nu este numărul de piese.

## Coordonate și precizie

**220 / 222** de rânduri au o localizare derivată din OpenStreetMap, dintre care
**59 sunt numai repere generale de stradă**. Nicio coordonată nu provine din PDF
și niciuna nu este prezentată ca măsurătoare verificată pe teren.

| Nivel | Valoare în export | Poziții |
| --- | --- | ---: |
| Obiect OSM asociat — punct | `osm_amenity_point` | 10 |
| Obiect OSM asociat — centru calculat | `osm_amenity_center` | 16 |
| Reper de parc | `park_reference` | 13 |
| Reper de școală | `school_reference` | 11 |
| Reper de bloc / clădire | `building_reference` | 83 |
| Reper de intersecție | `intersection_reference` | 28 |
| Reper general de stradă | `street_reference` | 59 |
| Fără coordonate | `unknown` | 2 |

GeoJSON este o colecție de **puncte WGS84** `[longitude, latitude]`, cu toate cele
222 de înregistrări. Cele două cazuri nerezolvate au `geometry: null`, iar
`latitude` și `longitude` sunt `null` în JSON și celule goale în CSV.

- `osm_amenity_*`: obiect de tipul potrivit, dedus ca asociere cu amplasamentul;
  o geometrie de suprafață este reprezentată printr-un centru calculat.
- `*_reference`: reper al parcului, școlii, blocului, intersecției sau străzii;
  nu reprezintă poziția exactă a echipamentului sau intrarea sa.
- `geocoding_reference_ids`, `geocoding_source_url`, `geocoding_method` și
  `geocoding_note` explică fiecare asociere. Încrederea este calitativă, nu o
  probabilitate sau o garanție metrică.
- Reperele generale de stradă sunt ascunse inițial pe hartă; le poți activa.
  Filtrele după categorie și precizie se aplică tabelului și hărții.
- Pozițiile care împart aceleași coordonate sunt grupate în popup, păstrându-și
  identificatorii separați în toate exporturile.

## Consum în curl, Python, Node.js și Go

Endpoint fără autentificare:

```text
https://baditaflorin.github.io/arad-recreation-data/data/records.json
```

Schimbă `records` cu `playgrounds`, `leisure`, `sports_fields` sau `dog_parks`
pentru o singură categorie; extensiile sunt `.csv`, `.json` și `.geojson`.

```bash
curl -fL https://baditaflorin.github.io/arad-recreation-data/data/records.csv -o records.csv
python examples/download.py
node examples/download.mjs
go run examples/download.go
```

Exemplele primesc opțional `JSON_URL OUTPUT_PATH` și verifică răspunsul HTTP
și structura JSON. Nu necesită chei API sau biblioteci suplimentare.

## Reproducere din surse reale

Python 3.10+; pentru parserul PDF se instalează pdfplumber:

```bash
python -m pip install -r requirements.txt

# Reextrage tabel cu tabel din PDF, geocodează din extrasul OSM păstrat,
# apoi regenerează toate exporturile. Nu folosește internetul.
python scripts/scrape_recons.py --offline
python scripts/build_site.py
python scripts/validate_exports.py
python -m unittest discover -s tests -v

# Actualizează documentul oficial, cu validare înainte de înlocuire.
python scripts/scrape_recons.py --refresh-source

# Actualizează extrasul OSM printr-o singură interogare Overpass,
# apoi reface asocierile și exporturile.
python scripts/geocode_osm.py --refresh
python scripts/build_site.py
```

`raw/source.json` și `raw/osm-source.json` păstrează SHA-256, data preluării și
URL-urile. Extrasul OSM este comprimat în `raw/osm-reference.json.gz`; manifestul
verifică SHA-256 al JSON-ului decomprimat. Interogarea este în `raw/osm-query.txt`.
Nu se fac 222 de cereri către un geocoder public. Toate regulile sunt în cod,
cu ancore spațiale și verificări pentru ambiguități.

Parserul verifică cele 107 pagini, forma tabelelor, identificatorii și secvențele
complete 1..81 / 1..116 / 1..21 / 1..4. La schimbarea documentului oprește
actualizarea; nu înlocuiește exporturile cu fișiere goale. Testele verifică
inclusiv cantitățile, duplicatele, sursele, cazurile nerezolvate și potrivirile
care ar confunda numele unei școli cu o piață din alt cartier.

## Decizii și probleme păstrate explicit

- Două poziții pentru câini au aceeași descriere „Parcul Strada Mărului”. Sunt
  păstrate separat; un reper comun nu stabilește două suprafețe distincte.
- La agrement 67, sursa scrie Cartierul Alfa, dar descrierea spune relocat pe
  Strada Simion Popa, Blocul 146. Cartierul sursei rămâne neschimbat; adresa veche
  de pe Strada Cărămidarilor este exclusă din geocodare.
- La locul de joacă 19-03, sursa scrie „Aleea Denza”. Geocodarea folosește
  „Aleea Dezna”, asociere susținută de OSM și de documentația municipală despre
  blocul X12: [HCL 274/2002](https://www.primariaarad.ro/dm_arad/2002/Hotarari.nsf/46C4A253904C31C9C22587740070348D/$FILE/h695.pdf?Open=).
  Textul sursei nu este corectat tacit.
- Variantele de nume precum George Byron / Byron, Brezoianu / Ioan Brezoianu,
  Romanța / Romanței și Ilarion Felea / Felea Ilarion sunt reguli vizibile de
  potrivire cu numele OSM, nu redenumiri oficiale.
- Cazuri nerezolvate: Strada Agronomului și Piața Mică, zona Funcționarilor.
  Nu sunt plasate arbitrar în centrul cartierului sau într-o altă piață omonimă.
- Terenurile de sport din Parcul Gai și de pe Strada Făt Frumos sunt menționate
  în nota sursei ca având alt administrator. Nu sunt inventate rânduri noi
  pentru a le adăuga celor 21 de poziții ale inventarului Recons.

## Sursa și licențele

Sursa oficială: [Caietul de sarcini, HCL 453/10.08.2026](https://www.primariaarad.ro/dm_arad/2026/hotarari.nsf/68ED6FAFE192EBCAC2258E51002CA3B3/$FILE/453%20H%20din%2010%2008%202026%20Anexa%203%20Caiet%20de%20sarcini.pdf?Open=).
Inventarele sunt la paginile PDF 96–104, numerotate separat în anexe.
Fiecare rând păstrează pagina, poziția în tabel și SHA-256 al documentului.

Codul propriu este MIT; Leaflet BSD-2-Clause. Îmbogățirea OSM este ODbL-1.0,
© OpenStreetMap contributors. Pentru inventarul oficial nu a fost identificată
o licență explicită open data; transformarea în formate deschise nu schimbă
condițiile sursei. Vezi [DATA_LICENSE.md](DATA_LICENSE.md).

Site-ul se publică din `main:/docs`, cu instrucțiuni în [PAGES.md](PAGES.md).
