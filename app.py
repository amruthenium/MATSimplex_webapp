"""
MATSimplex — Flask web app.
Front-end (templates/index.html) uploads MATSim network/plans/events; the server runs
matsim_to_s4d.py to harmonise them into the S4D layer bundle + a TwIS Turtle graph, and
answers the cross-layer analytical queries. Parsing happens server-side, so it is not
limited by browser memory.
"""
import os, uuid, json
from flask import Flask, request, jsonify, send_from_directory, render_template, abort
from werkzeug.utils import secure_filename
import pipeline

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
OUTPUTS = os.path.join(BASE, "outputs")
os.makedirs(UPLOADS, exist_ok=True); os.makedirs(OUTPUTS, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024   # 4 GB uploads

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/convert", methods=["POST"])
def convert():
    """Accept network/plans/events files + optional bbox; run the converter; return summary."""
    job = uuid.uuid4().hex[:12]
    updir = os.path.join(UPLOADS, job); outdir = os.path.join(OUTPUTS, job)
    os.makedirs(updir, exist_ok=True); os.makedirs(outdir, exist_ok=True)

    paths = {}
    for key in ("network", "plans", "events"):
        f = request.files.get(key)
        if f and f.filename:
            fn = secure_filename(f.filename); p = os.path.join(updir, fn)
            f.save(p); paths[key] = p

    if "network" not in paths:
        return jsonify(error="A network file is required."), 400

    bbox = None
    bb = request.form.get("bbox", "").strip()
    if bb:
        try:
            bbox = tuple(float(v) for v in bb.split(","))
            assert len(bbox) == 4
        except Exception:
            return jsonify(error="bbox must be LON_MIN,LON_MAX,LAT_MIN,LAT_MAX"), 400

    try:
        summary = pipeline.convert_bundle(
            network=paths.get("network"), plans=paths.get("plans"),
            events=paths.get("events"), out_dir=outdir, bbox=bbox)
        pipeline.build_ttl(outdir)
    except Exception as e:
        return jsonify(error=f"conversion failed: {e}"), 500

    # list produced files
    files = [f for f in os.listdir(outdir)]
    return jsonify(job=job, summary=summary, files=sorted(files))

@app.route("/api/queries/<job>")
def queries(job):
    outdir = os.path.join(OUTPUTS, secure_filename(job))
    if not os.path.isdir(outdir):
        return jsonify(error="unknown job"), 404
    return jsonify(pipeline.run_queries(outdir))

@app.route("/api/graph/<job>")
def graph(job):
    outdir = os.path.join(OUTPUTS, secure_filename(job))
    if not os.path.isdir(outdir):
        return jsonify(error="unknown job"), 404
    cls = request.args.get("cls", "link"); gid = request.args.get("id", "")
    depth = int(request.args.get("depth", 2)); limit = int (request.args.get("limit", 300))
    return jsonify(pipeline.build_graph_view(outdir, cls, gid, depth=depth, limit=limit))

@app.route("/api/geojson/<job>/<name>")
def geojson(job, name):
    """Serve a produced layer (nodes/links/activities.geojson) for the map."""
    outdir = os.path.join(OUTPUTS, secure_filename(job))
    name = secure_filename(name)
    if not os.path.exists(os.path.join(outdir, name)):
        abort(404)
    return send_from_directory(outdir, name)

@app.route("/download/<job>/<name>")
def download(job, name):
    outdir = os.path.join(OUTPUTS, secure_filename(job))
    return send_from_directory(outdir, secure_filename(name), as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
