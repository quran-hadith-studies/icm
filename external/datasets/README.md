# External datasets for the source-inference model

Downloaded 2026-10-10. Each source sits in its own directory and is kept exactly as
published. Nothing here has been merged or normalised yet.

What the model needs (see `../../README.md`, Method §1 and §3–5), and where it comes from:

| Model input | Shia corpus | Sunni corpus |
|---|---|---|
| witnesses: matn + isnād | Noor / Jāmiʿ al-Aḥādīth 4 (local, `hadith-hub/afreshte/data/jamiahadith4_hadith`) | `../hadith-kg.db`, `sanadset-650k/` |
| narrator identity per isnād slot | Noor `SanadInfo.SanadList[].Sanad[].RaviList[].RaviId` | `hadith-kg.db` `sanad_rawis.rawi_id`; Sanadset names → `zenodo-narrator-er/sanad_links.csv` |
| family grouping, independence groups $g(i)$ | Noor `GroupId` (one group = one narration copied across books), `jamiahadith4_hadith-cluster` | `hadith-kg.db` `meaning_groups` / `tarafs` |
| death year $d_v$ | Noor `InfoList` «وفات» (139 narrators only); tabaqa «طبقه در ثمانیه» (1,424) gives the Imam's era | hawramani (27,428), AR-Sanad (9,434), muslimscholars (4,289), hadith-kg |
| cities $L_v$ | Noor nisba in «لقب» (e.g. الكوفي، القمي) | AR-Sanad `living_city`/`journey_city`, muslimscholars `places_of_stay`/`death_place`, hadith-kg `iqama` |
| documented teachers $R_v$ | adjacent `RaviId` pairs in Noor's parsed isnāds (34,154 distinct pairs) | muslimscholars `teacher_ids`/`student_ids`, AR-Sanad `narrated_from`/`narrated_to`, hawramani teacher/student lists, hadith-kg isnāds |
| reliability (not used by the model yet) | Noor `InfoList2` «نتيجه ارزيابي»، «الفاظ جرح و تعدیل» | hadith-kg `aqwal`, hawramani `grades`, AR-Sanad ranks |

## Directories

### `zenodo-narrator-er/`
*Cross-Source Hadith Narrator Entity Resolution: Linked Biographical and Transmission-Graph
Data* (arXiv 2607.05424). Zenodo record 21019693,
`hadith_narrator_er_dataset.zip`, 138,417,766 bytes, md5 `607cbeffca65253697810734e2c5a569`
(verified). Unzipped into `extracted/zenodo/`:

| File | Rows / size | Contents |
|---|---|---|
| `hawramani_narrators.csv` | 100,915 / 857 MB | Ikram Hawramani's *Hadith Transmitters* encyclopedia: name, birth/death year AH (27,428 with a death year), grades, full biography text from 62 Sunni rijāl works |
| `muslimscholars_scholars.csv` | 25,247 / 7 MB | muslimscholars.info, scraped June 2026: birth/death year (4,289 with a Hijri death year) and place, places of stay, generation, grade, teacher and student ids |
| `narrator_links.csv` | 35 MB | hawramani ↔ muslimscholars links with confidence tier |
| `sanad_links.csv` | 35 MB | each Sanadset narrator string ↔ hawramani URL and muslimscholars id, with confidence tier |
| `narrator_nodes.csv`, `narrator_edges.csv` | 185,216 nodes / 814,093 edges | teacher → student graph over Sanadset name strings |

Licence: pipeline outputs CC BY 4.0; hawramani text public-domain sources, attribution
required; **muslimscholars redistribution terms unverified** — use locally, do not republish.

### `sanadset-650k/`
Mghari, Bouras, El Hibaoui, *Sanadset 650K: Data on Hadith Narrators*, Data in Brief 2022,
Mendeley Data `10.17632/5xth87zwb5.5`, CC BY 4.0. `sanadset.csv` (1,430,646,035 bytes):
650,986 hadiths from 926 Sunni books, each with book, number, matn and an XML-tagged
narrator list. `books.csv`, `hadith_samples.csv`. SHA-256 values are in
`SHA256SUMS`.

### `itqan/repo/`
Shallow clone of `github.com/R3GENESI5/Itqan`, commit
`199d870da5b726356b4a8dbaa096140cffb7d0ab` (2026-08-01). README states MIT; the repo has
no LICENSE file. Files relevant here:

| File | Contents |
|---|---|
| `src/arsanad_narrators.csv` | AR-Sanad 280K narrator DB (Information 13(2):55, 2022): 18,298 narrators (9,434 with a death year), death/birth year, `living_city`, `journey_city`, tabaqa, Ibn Ḥajar and al-Dhahabī ranks, `narrated_from`/`narrated_to` id lists |
| `src/kaggle_rawis.csv` | the Kaggle *Hadith Narrators (+24K)* table (fahd09, from muslimscholars.info) — older snapshot of `muslimscholars_scholars.csv` |
| `src/external_narrators_db.json`, `app/data/rijal/*.json` | Itqan's merged profiles (115k) |
| `app/data/shia/*` | Shia hadith texts (from Thaqalayn); no narrator ids |

### `openiti-shia-rijal/`
Twelve Shia rijāl works from the OpenITI corpus (release 2025.1.9, primary versions), fetched
from the `github.com/OpenITI/<NNNN>AH` repositories on 2026-10-10. Licence CC BY-NC-SA 4.0.
OpenITI mARkdown text; where the editors have marked biographies, each entry starts with
`### $` (count in the last column). Checksums in `SHA256SUMS`.

| File | Work | `### $` entries |
|---|---|---:|
| `0274AhmadBarqi.Rijal…` | رجال البرقي | unmarked |
| `0329IbnCumarKashshi.Rijal…` | رجال الكشي | unmarked |
| `0450Najashi.Rijal….mARkdown` | رجال النجاشي | 1,269 |
| `0460ShaykhTusi.Fihrist….mARkdown` | الفهرست | 910 |
| `0460ShaykhTusi.IkhtiyarMacrifatRijal….mARkdown` | اختيار معرفة الرجال | unmarked |
| `0460ShaykhTusi.Rijal….mARkdown` | رجال الطوسي (arranged by the Imam each narrator transmitted from) | 6,429 |
| `0500IbnHusaynGhadairiWasiti.Rijal….completed` | رجال ابن الغضائري | 225 |
| `0726CallamaHilli.KhulasatAqwal….completed` | خلاصة الأقوال | 1,758 |
| `0726CallamaHilli.Rijal.Masaha004722-ara1` | a second edition of the Khulāṣa | unmarked |
| `0740IbnDawudHilli.Rijal….completed` | رجال ابن داود | 2,970 |
| `1100MustafaTafrishi.NaqdRijal….completed` | نقد الرجال | 6,614 |
| `1101MuhammadCaliArdabili.JamicRuwat…` | جامع الرواة (for each narrator, who narrates from and to him in the Four Books) | unmarked |

The full corpus is also on Zenodo (record 18613982, `OpenITI-pri-data_v9.zip`, 2.9 GB, not
downloaded); its metadata TSV lists every title.

### `althurayya/`
al-Ṯurayyā gazetteer (Romanov & Seydi), `github.com/althurayya/althurayya.github.io`,
Apache-2.0. `places.geojson`: 2,518 places from Cornu's *Atlas du monde arabo-islamique*, each
with coordinates, coordinate certainty, Arabic and transliterated toponyms, and region.
`routes.json`: the route network between them. Used to geocode narrator cities
(L_v) and to measure distance between them.

### `shia-rijal-eshia/14036/`
معجم رجال الحديث (al-Khoei), 24 volumes, fetched page by page from `lib.eshia.ir/14036/<vol>/<page>`
with the hub's polite fetcher (`narrators/rijal_fetch.py`, resumable; manifest.json records
the last page of every volume). Raw HTML, one file per page. Parsed by
`narrators/rijal_parse.py::parse_eshia_khoei` (entries are the numbered «N - name:»
paragraphs; the numbering runs through the whole book, which is how footnotes and the
introduction's numbered sections are told apart).

### `wikishia/{fa,ar,en}/`
One JSON per crawled WikiShia page (page id, title, template, wikitext, langlinks), from
the MediaWiki API, for the narrator / companion / Shia-scholar infobox templates listed
in `narrators/data/wikishia_fields.json`. CC BY-SA (WikiShia's licence). Loaded into the
`wikishia` table by `narrators/wikishia_crawl.py`.

### `thaqalayn/narrators/`
ThaqalaynData `people/narrators/<id>.json` and `index.json` (CC0): 4,313 narrator ids over
al-Kāfī, Tahdhīb, Istibṣār, Amālī al-Ṣadūq and Kāmil al-ziyārāt with the chain fragments
each one occurs in. No biographies; used as an independent check of Noor's chain
resolution (`thaqalayn_adjacency`) and as an id bridge.

## Checked, not downloaded

| Source | What it has | Why not downloaded |
|---|---|---|
| WikiShia (`fa.wikishia.net`, MediaWiki API) | infobox templates «جعبه اطلاعات راوی» (142 pages), «صحابه» (186), «عالمان شیعه» (827): death year, birthplace, place of residence, Imams narrated from, teachers, students, reliability | needs a crawl (~1,150 API calls); small but well curated, covers the main figures |
| Wikidata (SPARQL) | 2,698 people with occupation muhaddith (Q1172458), 1,729 with a death date, 1,182 with a place of death or residence; only ~73 tagged Shia | thin for Shia narrators; no WikiShia identifier property |
| Multi-IsnadSet (Mendeley `gzprcr93zn`) | Ṣaḥīḥ Muslim isnāds, 2,092 narrators | no dates or places |
| KASHAF (`github.com/OmarShafie/hadith`) | 18,800 Sunni narrators | already merged into Itqan |
| muhaddithat/isnad-datasets | female narrators, muslimscholars ids | no dates or places beyond muslimscholars |

## Already local, not in this directory

* `../hadith-kg.db` — Hadith Knowledge Graph (CC BY 4.0), 49,844 Sunni narrators, 577,024
  isnāds with resolved `rawi_id`.
* `hadith-hub/afreshte/data/` — the full Noor (Jāmiʿ al-Aḥādīth 4) Elasticsearch extraction.
  The server exposes no separate rijāl index (checked `_cat/indices` 2026-10-10); all narrator
  data is the `SanadInfo` embedded in each hadith: 102,404 of 433,672 hadiths (53 of 242
  books, essentially the Four Books, Wasāʾil and the early Shia collections), 8,120 distinct
  `RaviId`, 1,606,321 narrator slots, of which 39,935 carry more than one candidate and
  38,722 none.

## Known gap

No open Shia rijāl dataset gives death years and cities as data fields. Noor gives death years
for only 139 narrators. What exists: the rijāl texts in `openiti-shia-rijal/` (dates and
places have to be extracted from each entry), WikiShia infoboxes for about 1,150 figures, and
Noor's tabaqa field. Of the later dictionaries, معجم رجال الحديث (Khoei) is not in OpenITI;
it, طبقات أعلام الشيعة and قاموس الرجال are catalogued in `hadith-hub/catalog/eshia_books.json`
and `ablibrary_books.json`. Only الفهرست (rezwan14010), تنقيح المقال (rezwan10510) and
طبقات أعلام الشيعة (rezwan13470) are downloaded in the hub.
