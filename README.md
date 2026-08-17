# MATSimplex: deployable web app

All the scripts, mostly in Python but some in JavaScript, and HTML, used in my Master's thesis project at TUM and Simplex4Data GmbH. 

What is this project about?
Multi-Agent Transport Simulation (MATSim) is a leading open-source framework for microscopic, agent-based transport simulations. It is increasingly used in research and practice to support
data-driven mobility and urban planning. In typical projects, MATSim output consists of large collections of XML, CSV and spatial files (events, trajectories, link statistics, accessibility indicators) that are tailored to the needs of transport modellers but difficult to interpret and reuse for non-experts such as planners, policy makers or stakeholders in other domains.
This heterogeneity creates several challenges: outputs from different runs and scenarios are organised in ad hoc folder structures, indicators are computed using custom scripts, and there is no common semantic layer that would enable cross-scenario comparison, reproducible analysis pipelines, or integration with other urban data sources. At the same time, frameworks such as eqasim demonstrate how standardised, staged pipelines from raw data to MATSim scenarios improve reproducibility and transparency on the input side, but similar attention is rarely given to the
systematic management of outputs.
This thesis aims to design and prototype a reusable data management and harmonisation workflow for MATSim output data using the TwIS framework and its Simplex4Data data warehouse as a central, GraphDB-based store, complemented by a semantic transport and knowledge graph representation of key MATSim concepts (agents, activities, links, indicators). The overarching goal is to make MATSim results more accessible, queryable, and interpretable for both technical and non-technical users, while preserving provenance information to support reproducible, scenario-based analysis.

Harmonises MATSim output (network, plans, events) into a Simplex4Data-importable layer bundle **and** a TwIS RDF graph, then answers cross-layer analytical queries.

## Structure
```
matsimplex_app/
├── app.py               Flask app (routes / API)
├── pipeline.py          server-side wrapper: convert + build TTL + run queries
├── matsim_to_s4d.py     the CORE converter (network/plans/events -> layers)
├── templates/index.html front-end (upload → area → convert → explore → query)
├── static/              (assets, if any)
├── uploads/  outputs/   per-job working dirs (created at runtime)
└── requirements.txt
```

## Run locally
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py                      # http://localhost:5000
```

## Run in production
```bash
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```
## API
- `POST /api/convert`  — multipart: `network`, `plans`, `events` files + `bbox` field
   (`LON_MIN,LON_MAX,LAT_MIN,LAT_MAX`). Returns `{job, summary, files}`.
- `GET  /api/queries/<job>`        — the four cross-layer queries as JSON.
- `GET  /api/geojson/<job>/<name>` — a produced layer for the map.
- `GET  /download/<job>/<name>`    — download any produced file (graph.ttl, *.geojson, *.csv).

## The CLI still works
`matsim_to_s4d.py` remains runnable standalone:
```bash
python matsim_to_s4d.py --network net.xml.gz --plans plans.xml.gz --events events.xml.gz \
  --out-dir out --bbox "11.55,11.60,48.13,48.16"
```

## Streaming / memory
The converter streams all three files with **constant, low memory** (measured ~44 MB peak on
the 196 MB Munich network), via: filter-to-district during the scan, incremental writes,
line/regex scan for events, and lxml sibling-deletion. It reads `.xml` or `.xml.gz`.
**Always pass a `--bbox` / pick a district** for large inputs so only that slice is kept.

## For large uploads through the web UI
The Flask dev server (`python app.py`) is for small files only. For big uploads use waitress
(Windows) or gunicorn (Linux/Mac) with raised limits:
```
# Windows
python -m waitress --listen=0.0.0.0:5000 --max-request-body-size=8589934592 --channel-timeout=1800 app:app
# Linux/Mac
gunicorn -w 2 -b 0.0.0.0:5000 --timeout 1800 app:app
```
Even so, prefer gzipped files and a district bbox. For the full multi-GB scenario the CLI is
most reliable (no upload):
```
python matsim_to_s4d.py --network net.xml.gz --plans plans.xml.gz --events events.xml.gz --out-dir out --bbox "11.530,11.560,48.108,48.130"
```

## Notes
- The four demonstration queries (busiest links, busiest intersections, route popularity, delay × road class) are computed over the harmonised bundle and prove
  cross-layer harmonisation (e.g. delay×class needs *events* realised time + *network*
  free-flow speed + road class together).
- For very large inputs, pass a `bbox` to clip to a district.
- `graph.ttl` is produced for loading into GraphDB; the GeoJSON/CSV bundle is for
  Simplex4Data import.
