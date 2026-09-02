#!/usr/bin/env python3
"""
batch_eval.py -- run the MATSim converter across several named neighbourhood bboxes
and collect evaluation statistics into a table (Markdown + CSV).

Usage:
  python batch_eval.py --network net.xml.gz --plans plans.xml.gz --events events.xml.gz

Edit the NEIGHBOURHOODS dict below with your districts and their bboxes
(LON_MIN,LON_MAX,LAT_MIN,LAT_MAX in WGS84).
"""
import os, time, argparse, tracemalloc, glob, csv as csvmod
import matsim_to_s4d as conv

# ---- neighbourhoods and their bounding boxes (WGS84) ----
NEIGHBOURHOODS = {
    #"Sendling":      (11.530, 11.560, 48.108, 48.130),
    #"Maxvorstadt":   (11.560, 11.585, 48.145, 48.160),
    #"Schwabing":     (11.575, 11.600, 48.155, 48.175),
    #"Altstadt/Centre": (11.56, 11.59, 48.13,  48.148),
    #"Haidhausen":     (11.59,  11.62, 48.125, 48.145),
    #"Central(wide)":  (11.5,11.64,48.1,48.18),
    #"Neuhausen-Nymphenburg": (11.495,11.54,48.15,48.1833),
    #"Laim":           (11.495,11.54,48.1167,48.15),
    #"Sendling-Westpark/Sendling": (11.495,11.54,48.0833,48.1167),
    "Untergiesing-Harlaching": (11.54,11.585,48.0833,48.1167),
    "Obergiesing-Fasangarten+Ramersdorf-Perlach": (11.585,11.63,48.0833,48.1167),
    # add more: "Name": (lon_min, lon_max, lat_min, lat_max),
}

def folder_size(path, patterns):
    total = 0
    for pat in patterns:
        for f in glob.glob(os.path.join(path, pat)):
            total += os.path.getsize(f)
    return total

def human(n):
    for unit in ["B","KB","MB","GB"]:
        if n < 1024: return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--network"); ap.add_argument("--plans"); ap.add_argument("--events")
    ap.add_argument("--out-root", default="eval_out")
    ap.add_argument("--scenario", default="Munich baseline")
    ap.add_argument("--iteration", default="0")
    a = ap.parse_args()

    rows = []
    for name, bbox in NEIGHBOURHOODS.items():
        out_dir = os.path.join(a.out_root, name.replace(" ", "_"))
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n>>> {name}  bbox={bbox}")

        tracemalloc.start()
        t0 = time.time()
        summary = conv.convert_all(
            network=a.network, plans=a.plans, events=a.events,
            out_dir=out_dir, bbox=bbox)
        elapsed = time.time() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        csv_geojson = folder_size(out_dir, ["*.csv", "*.geojson"])
        ttl = folder_size(out_dir, ["*.ttl"])   # 0 if converter doesn't emit ttl

        row = {
            "Neighbourhood": name,
            "Nodes": summary.get("nodes", 0),
            "Links": summary.get("links", 0),
            "Activities": summary.get("activities", 0),
            "Trips": summary.get("trips", 0),
            "Output size (CSV+GeoJSON)": human(csv_geojson),
            ".ttl size": human(ttl) if ttl else "-",
            "Peak memory": human(peak),
            "Processing time (s)": f"{elapsed:.1f}",
        }
        rows.append(row)
        print("   " + "  ".join(f"{k}={v}" for k,v in row.items() if k!="Neighbourhood"))

    # ---- write outputs ----
    cols = list(rows[0].keys())
    # CSV
    with open("eval_table.csv", "w", newline="") as f:
        w = csvmod.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    # Markdown
    with open("eval_table.md", "w") as f:
        f.write("| " + " | ".join(cols) + " |\n")
        f.write("|" + "|".join(["---"]*len(cols)) + "|\n")
        for r in rows:
            f.write("| " + " | ".join(str(r[c]) for c in cols) + " |\n")

    print("\n=== eval_table.md ===")
    print(open("eval_table.md").read())
    print("Wrote eval_table.csv and eval_table.md")

if __name__ == "__main__":
    main()
