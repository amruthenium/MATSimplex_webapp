"""
pipeline.py — server-side wrapper around matsim_to_s4d.py.
Runs the tested converter functions and also builds a TwIS Turtle graph and the
native analytical queries, so the Flask app can do everything the browser demo did
but WITHOUT the browser memory ceiling (parsing happens here, in Python).
"""
import os, json, csv, io, gzip
import matsim_to_s4d as conv          # the core converter (network/plans/events -> layers)

# ----- run the converter bundle to an output dir -----
def convert_bundle(network=None, plans=None, events=None, out_dir=".", bbox=None):
    """bbox = (lon_min,lon_max,lat_min,lat_max) or None. Streams all three files with
    constant memory (filter-to-district during scan, incremental writes)."""
    os.makedirs(out_dir, exist_ok=True)
    return conv.convert_all(network=network, plans=plans, events=events, out_dir=out_dir, bbox=bbox)

# ----- build a TwIS Turtle graph from the emitted bundle (for GraphDB / download) -----
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
def build_ttl(out_dir):
    def load(p):
        fp = os.path.join(out_dir, p)
        return json.load(open(fp)) if os.path.exists(fp) else {"features": []}
    nodes = load("nodes.geojson"); links = load("links.geojson"); acts = load("activities.geojson")
    lines = ["@prefix twis: <https://w3id.org/twis#> .",
             "@prefix mun: <https://example.org/matsim/munich/> .",
             "@prefix geo: <http://www.opengis.net/ont/geosparql#> .",
             "@prefix sf: <http://www.opengis.net/ont/sf#> .",
             "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .", ""]
    for f in nodes["features"]:
        i = f["properties"]["id"]; lon, lat = f["geometry"]["coordinates"]
        lines.append(f'mun:knoten_{i} a twis:Strassenknotenpunkt ; geo:hasGeometry mun:g_k_{i} .')
        lines.append(f'mun:g_k_{i} a sf:Point ; geo:asWKT "<{CRS84}> POINT({lon} {lat})"^^geo:wktLiteral .')
    for f in links["features"]:
        p = f["properties"]; i = p["id"]; c = f["geometry"]["coordinates"]
        a, z = c[0], c[-1]
        lines.append(f'mun:ast_{i} a twis:Ast ; twis:laenge "{p.get("laenge","")}"^^xsd:double ; '
                     f'twis:freespeed "{p.get("freespeed","")}"^^xsd:double ; twis:klassifizierung "{p.get("klassifizierung","")}" ; '
                     f'twis:vonKnoten mun:knoten_{p.get("von")} ; twis:nachKnoten mun:knoten_{p.get("nach")} ; geo:hasGeometry mun:g_a_{i} .')
        lines.append(f'mun:g_a_{i} a sf:LineString ; geo:asWKT "<{CRS84}> LINESTRING({a[0]} {a[1]}, {z[0]} {z[1]})"^^geo:wktLiteral .')
    for f in acts["features"]:
        p = f["properties"]; i = p["id"]; lon, lat = f["geometry"]["coordinates"]
        lines.append(f'mun:activity_{i} a twis:Activity ; twis:typ "{p.get("type","")}" ; geo:hasGeometry mun:g_act_{i} .')
        lines.append(f'mun:g_act_{i} a sf:Point ; geo:asWKT "<{CRS84}> POINT({lon} {lat})"^^geo:wktLiteral .')
    ttl = "\n".join(lines) + "\n"
    open(os.path.join(out_dir, "graph.ttl"), "w").write(ttl)
    return ttl

# ----- native analytical queries over the emitted bundle (cross-layer proof) -----
def _read_csv(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []

def run_queries(out_dir):
    links = {f["properties"]["id"]: f["properties"]
             for f in (json.load(open(os.path.join(out_dir, "links.geojson")))["features"]
                       if os.path.exists(os.path.join(out_dir, "links.geojson")) else [])}
    trips = _read_csv(os.path.join(out_dir, "trips.csv"))
    routes = _read_csv(os.path.join(out_dir, "routes.csv"))
    trip_links = _read_csv(os.path.join(out_dir, "trip_links.csv"))

    # busiest links (events, fallback routes)
    usage = {}
    if trips:
        for t in trips:
            for lid in t.get("link_sequence", "").split():
                usage[lid] = usage.get(lid, 0) + 1
    elif routes:
        for r in routes:
            for lid in r.get("link_sequence", "").split():
                usage[lid] = usage.get(lid, 0) + 1
    busiest = sorted(({"link": k, "trips": v, "road_class": links.get(k, {}).get("klassifizierung", "—")}
                      for k, v in usage.items()), key=lambda x: -x["trips"])[:15]

    # busiest intersections (degree>=3 by through-traffic)
    deg, thru = {}, {}
    for lid, p in links.items():
        for n in (p.get("von"), p.get("nach")):
            deg[n] = deg.get(n, 0) + 1
            thru[n] = thru.get(n, 0) + usage.get(lid, 0)
    nodes_busy = sorted(({"node": n, "degree": d, "through": thru.get(n, 0)}
                         for n, d in deg.items() if d >= 3), key=lambda x: -x["through"])[:15]

    # route popularity
    seqs = {}
    src = trips if trips else routes
    for x in src:
        s = x.get("link_sequence", "")
        seqs[s] = seqs.get(s, 0) + 1
    popular = sorted(({"n_links": len(s.split()), "count": c} for s, c in seqs.items()),
                     key=lambda x: -x["count"])[:12]

    # delay x road class (events x network) — the flagship cross-layer query
    agg = {}
    for tl in trip_links:
        lid = tl.get("link_id"); tt = tl.get("traveltime_s")
        if not tt:
            continue
        try:
            realised = float(tt)
        except ValueError:
            continue
        lk = links.get(lid)
        if not lk:
            continue
        try:
            length = float(lk.get("laenge")); fs = float(lk.get("freespeed"))
        except (TypeError, ValueError):
            continue
        if fs <= 0:
            continue
        free = length / fs
        if free <= 0:
            continue
        typ = lk.get("klassifizierung") or "—"
        a = agg.setdefault(typ, {"s": 0.0, "c": 0})
        a["s"] += realised / free; a["c"] += 1
    delay = sorted(({"road_class": t, "delay_factor": round(a["s"] / a["c"], 2), "n": a["c"]}
                    for t, a in agg.items()), key=lambda x: -x["delay_factor"])

    return {"busiest_links": busiest, "busiest_intersections": nodes_busy,
            "route_popularity": popular, "delay_by_class": delay}

#knowledge graph viewer

def _graph_adjacency(out_dir):
    adj = {}
    def edge(x,y):
        adj.setdefault(x, set()).add(y); adj.setdefault(y,set().add(x))
    lp = os.path.join(out_dir, "links.geojson")
    if os.path.exists(lp):
        for f in json.load(open(lp))["features"]:
            p = f["properties"]; L ="ast:" + str(p["id"])
            if p.get("von") is not None: edge(L, "knoten:" + str(p["von"]))
            if p.get("nach") is not None: edge(L, "knoten:" + str(p["nach"]))
    ap = os.path.join(out_dir, "activities.geojson")
    if os.path.exists(ap):
        for f in json.load(open(ap))["features"]:
            p = f["properties"]
            if p.get("link_id"): edge("act:" + str(p["id"]), "ast:" + str(p["link_id"]))
    for fn, pref in (("trips.csv", "trip"), ("routes.csv", "route")):
        fp = os.path.join(out_dir, fn)
        if os.path.exists(fp):
            for r in csv.DictReader(open(fp)):
                base = pref + ":" + r["id"]
                for lid in (r.get("link_sequence", "") or "").split():
                    edge(base, "ast:" + lid)
    return adj

_PREFIX = {"link": "ast", "node": "knoten", "activity": "act", "trip": "trip", "route": "route"}
def build_graph_view(out_dir, start_cls, start_id, depth=2, limit=300): #change depth toggle can be nice
    pref = _PREFIX.get(start_cls, start_cls)
    start = f"{pref}:{start_id}"
    adj = _graph_adjacency(out_dir)
    if start not in adj:
        return {"nodes": [], "edges": [], "note": f"{start} not found"}
    seen = {start : 0}; frontier = [start]; order = [start]; d = 0
    while frontier and d < depth and len(order) < limit:
        nxt =[]
        for u in frontier:
            for v in adj.get(u,()):
                if v not in seen:
                    seen[v] = d+1; order.append(v); nxt.append(v)
                    if len(order) >= limit: break
            if len(order)>= limit: break
        frontier = nxt; d +=1 #frontier node loop
    keep = set(order)
    def typ(n): return n.split(":", 1)[0]
    nodes = [{"id": n, "label": n.split(":", 1)[1], "cls": typ(n), "depth": seen[n]} for n in order]
    edges = []; emitted = set()
    for u in order:
        for v in adj.get(u,()):
            if v in keep:
                k = tuple(sorted((u,v)))
                if k not in emitted: emitted.add(k); edges.append({"source": u, "target":v})
    return {"nodes": nodes,"edges": edges, "start": start,"depth": depth}



