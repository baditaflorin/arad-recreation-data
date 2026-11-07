# GitHub Pages

Site public: https://baditaflorin.github.io/arad-recreation-data/
Repository: https://github.com/baditaflorin/arad-recreation-data

Pages folosește **Deploy from a branch → main → /docs**. Fișierul .nojekyll
permite servirea directă a fișierelor statice. Nu este necesar un server sau
un workflow GitHub Actions pentru extragerea datelor la accesarea site-ului.

## Actualizare locală

```bash
python -m pip install -r requirements.txt
python scripts/scrape_recons.py --offline
python scripts/build_site.py
python scripts/validate_exports.py
python -m unittest discover -s tests -v
python -m http.server --directory docs 8000
```

Deschide http://localhost:8000 . După verificare, commit și push pe main,
inclusiv docs/ și copiile actualizate ale datelor. Pages publică folderul docs/.

Pentru o copie proprie: creează un repository public și selectează în
Settings → Pages aceeași sursă main /docs. Schimbă URL-urile din exemple,
README și scripts/build_site.py dacă schimbi numele sau proprietarul repo-ului.

Cele patru exemple din examples/ și docs/examples/ se execută în Python,
Go, Node.js și curl; primesc un URL JSON și o cale de ieșire ca argumente.
