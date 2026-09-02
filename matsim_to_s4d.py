#!/usr/bin/env python3
"""
matsim_to_s4d.py — STREAMING MATSim (network/plans/events) -> Simplex4Data bundle.

Constant, low memory even on multi-GB inputs, via four techniques:
  1. filter-to-district DURING the scan (discard non-district records immediately)
  2. incremental writes (stream each feature/row straight to disk; never hold lists)
  3. line/regex scan for events (no DOM tree at all)
  4. lxml iterparse + clear + delete preceding siblings (no memory creep)

Reads .xml or .xml.gz (streamed decompression). Network is parsed first to learn the
district's link ids; plans/events are then filtered against that set as they stream.

    python matsim_to_s4d.py --network net.xml.gz --plans plans.xml.gz --events events.xml.gz \
        --out-dir out --bbox "11.530,11.560,48.108,48.130"
"""
import os, gzip, io, json, csv, argparse, time, re
from lxml import etree
from pyproj import Transformer

_tw = Transformer.from_crs("EPSG:31468", "EPSG:4326", always_xy=True)   # -> lon,lat
def wgs(x, y): lon, lat = _tw.transform(float(x), float(y)); return (round(lon, 7), round(lat, 7))
def _num(v):
    try: return float(v)
    except (TypeError, ValueError): return v
def open_bin(p):  return gzip.open(p, "rb") if p.endswith(".gz") else open(p, "rb")
def open_text(p): return io.TextIOWrapper(gzip.open(p, "rb") if p.endswith(".gz") else open(p, "rb"),
                                          encoding="utf-8", errors="ignore")
def _clear(e):
    e.clear()
    while e.getprevious() is not None:
        del e.getparent()[0]

# ---- incremental writers (stream to disk, hold nothing) ----
class GJ:
    def __init__(self, path):
        self.f = open(path, "w"); self.f.write('{"type":"FeatureCollection","features":['); self.first = True; self.n = 0
    def add(self, geom, props):
        if not self.first: self.f.write(",")
        self.f.write(json.dumps({"type": "Feature", "geometry": geom, "properties": props})); self.first = False; self.n += 1
    def close(self):
        self.f.write("]}"); self.f.close()
class CW:
    def __init__(self, path, cols, meta=None):
        self.meta = meta or {}
        self.f = open(path, "w", newline=""); self.w = csv.DictWriter(self.f, fieldnames=cols); self.w.writeheader(); self.cols = cols; self.n = 0
    def add(self, row):
        r = {k: ("" if row.get(k) is None else row.get(k)) for k in self.cols}
        r.update(self.meta); self.w.writerow(r); self.n += 1
    def close(self):
        self.f.close()

class CWKT:
    def __init__(self, path, cols, meta=None):
        self.meta = meta or {}; self.metavals = list(self.meta.values())
        self.f = open(path, "w", newline=""); self.w = csv.DictWriter(self.f)
        self.cols = cols; self.w.writerow(cols+ list(self.meta.keys()) +["geom"]); self.n = 0
    def add(self, row, wkt):
        self.w.writerow([("" if row.get(c) is None else row.get(c)) for c in self.cols]
                        + self.metavals + [wkt]); self.n += 1
    def close(self):
        self.f.close()

# ---- network: single streaming pass; build district link-set; write nodes/links ----
def convert_network(src, out_dir, bbox):
    coords = {}; district = set(); kept_nodes = set()
    linkw = GJ(os.path.join(out_dir, "links.geojson"))
    ctx = etree.iterparse(open_bin(src), events=("end",), tag=("node", "link"))
    for _, e in ctx:
        if e.tag == "node":
            ll = wgs(e.get("x"), e.get("y"))
            if not bbox or (bbox[0] <= ll[0] <= bbox[1] and bbox[2] <= ll[1] <= bbox[3]):
                coords[e.get("id")] = ll
        else:  # link (nodes always precede links, so coords is complete here)
            frm, to = e.get("from"), e.get("to")
            if frm in coords and to in coords:
                at = {a.get("name"): a.text for a in e.iter("attribute")}
                lid = e.get("id")
                linkw.add({"type": "LineString", "coordinates": [list(coords[frm]), list(coords[to])]},
                          {"id": lid, "von": frm, "nach": to, "laenge": _num(e.get("length")),
                           "freespeed": _num(e.get("freespeed")), "kapazitaet": _num(e.get("capacity")),
                           "klassifizierung": at.get("type", ""), "origid": at.get("origid", "")})
                district.add(lid); kept_nodes.add(frm); kept_nodes.add(to)
        _clear(e)
    linkw.close();
    nodew = GJ(os.path.join(out_dir, "nodes.geojson"))
    for nid in kept_nodes:
        nodew.add({"type": "Point", "coordinates": list(coords[nid])}, {"id": nid})
    nodew.close()
    return nodew.n, linkw.n, district

# ---- plans: stream person-by-person; keep bbox activities + district-touching routes ----
def convert_plans(src, out_dir, bbox, district):
    actw = GJ(os.path.join(out_dir, "activities.geojson"))
    routew = CW(os.path.join(out_dir, "routes.csv"), ["id", "mode", "n_links", "link_sequence"])
    persons = plans = legs = 0
    ctx = etree.iterparse(open_bin(src), events=("end",), tag="person")
    for _, person in ctx:
        pid = person.get("id"); persons += 1
        for pi, plan in enumerate(person.findall("./plan"), 1):
            plans += 1; seq = 0
            for el in plan:
                tag = etree.QName(el).localname
                if tag == "activity":
                    x, y = el.get("x"), el.get("y")
                    if x and y:
                        ll = wgs(x, y)
                    if not bbox or (bbox[0] <= ll[0] <= bbox[1] and bbox[2] <= ll[1] <= bbox[3]):
                            actw.add({"type": "Point", "coordinates": list(ll)},
                                     {"id": f"{pid}_{pi}_{seq}", "type": el.get("type"), "link_id": el.get("link"),
                                      "start_time":el.get("start_time"), "end_time":el.get("end_time")})
                    seq += 1
                elif tag == "leg":
                    legs += 1
                    r = el.find("./route")
                    if r is not None and r.text:
                        links = r.text.split()
                        if not district or any(l in district for l in links):
                            routew.add({"id": f"{pid}_{pi}_{seq}", "mode": el.get("mode"),
                                        "n_links": len(links), "link_sequence": " ".join(links)})
                    seq += 1
        _clear(person)
    actw.close(); routew.close()
    return persons, plans, actw.n, legs, routew.n

# ---- events: line/regex scan (no DOM); keep district-touching trips ----
def convert_events(src, out_dir, district):
    tripw = CW(os.path.join(out_dir, "trips.csv"),
               ["id", "person", "mode", "dep_s", "arr_s", "duration_s", "n_links", "link_sequence"])
    tlw = CW(os.path.join(out_dir, "trip_links.csv"),
             ["trip_id", "position", "link_id", "enter_s", "leave_s", "traveltime_s"])
    veh2p = {}; legs = {}; seqc = {}
    reType = re.compile(r'type="([^"]+)"'); reTime = re.compile(r'time="([^"]+)"')
    rePerson = re.compile(r'person="([^"]+)"'); reVeh = re.compile(r'vehicle="([^"]+)"')
    reLink = re.compile(r'link="([^"]+)"'); reMode = re.compile(r'legMode="([^"]+)"')
    def A(ln, rx): m = rx.search(ln); return m.group(1) if m else None
    f = open_text(src)
    for ln in f:
        typ = A(ln, reType)
        if not typ: continue
        ts = A(ln, reTime); t = float(ts) if ts else None
        if typ == "departure":
            p = A(ln, rePerson); legs[p] = {"mode": A(ln, reMode), "dep": t, "trav": []}
        elif typ == "PersonEntersVehicle":
            veh2p[A(ln, reVeh)] = A(ln, rePerson)
        elif typ in ("vehicle enters traffic", "entered link"):
            p = veh2p.get(A(ln, reVeh)); lk = A(ln, reLink)
            if p in legs: legs[p]["trav"].append([lk, t, None])
        elif typ in ("left link", "vehicle leaves traffic"):
            p = veh2p.get(A(ln, reVeh))
            if p in legs and legs[p]["trav"]: legs[p]["trav"][-1][2] = t
        elif typ == "PersonLeavesVehicle":
            veh2p.pop(A(ln, reVeh), None)
        elif typ == "arrival":
            p = A(ln, rePerson); leg = legs.pop(p, None)
            if not leg: continue
            links = [r[0] for r in leg["trav"]]
            if district and not any(l in district for l in links):
                continue
            seqc[p] = seqc.get(p, 0) + 1; tid = f"{p}_{seqc[p]}"
            tripw.add({"id": tid, "person": p, "mode": leg["mode"], "dep_s": leg["dep"], "arr_s": t,
                       "duration_s": (t - leg["dep"]) if (t is not None and leg["dep"] is not None) else "",
                       "n_links": len(links), "link_sequence": " ".join(links)})
            for pos, (lk, en, lv) in enumerate(leg["trav"]):
                tlw.add({"trip_id": tid, "position": pos, "link_id": lk, "enter_s": en, "leave_s": lv,
                         "traveltime_s": (lv - en) if (lv is not None and en is not None) else ""})
    f.close(); tripw.close(); tlw.close()
    return tripw.n, tlw.n

# ---- orchestrator: network first (district set) -> plans -> events ----
def convert_all(network=None, plans=None, events=None, out_dir=".", bbox=None, scenario_name=None, iteration=None, version=None, CRS=None, encoding=None):
    os.makedirs(out_dir, exist_ok=True)
    meta = {}
    if iteration is not None:
        meta["iteration"] = iteration
    if version is not None:
        meta["version"] = version
    if CRS is not None:
        meta["CRS"] = CRS
    if encoding is not None:
        meta["encoding"] = encoding
    summary = {}; district = set()
    if network:
        n, l, district = convert_network(network, out_dir, bbox)
        summary["nodes"], summary["links"] = n, l
    if plans:
        pe, pl, ac, lg, ro = convert_plans(plans, out_dir, bbox, district)
        summary.update(persons=pe, plans=pl, activities=ac, legs=lg, routes=ro)
    if events:
        tr, tl = convert_events(events, out_dir, district)
        summary.update(trips=tr, trip_links=tl)
    return summary

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--network"); ap.add_argument("--plans"); ap.add_argument("--events")
    ap.add_argument("--out-dir", default="."); ap.add_argument("--bbox", help="LON_MIN,LON_MAX,LAT_MIN,LAT_MAX")
    ap.add_argument("--scenario-name"); ap.add_argument("--iteration"); ap.add_argument("--version"); ap.add_argument("--CRS"); ap.add_argument("--encoding")
    a = ap.parse_args()
    if not any([a.network, a.plans, a.events]):
        ap.error("give at least one of --network/--plans/--events")
    bbox = tuple(float(v) for v in a.bbox.split(",")) if a.bbox else None
    t0 = time.time()
    s = convert_all(a.network, a.plans, a.events, a.out_dir, bbox, a.scenario_name, a.iteration, a.version, a.CRS, a.encoding)
    print("summary:", s, f"({time.time()-t0:.1f}s) -> {a.out_dir}")