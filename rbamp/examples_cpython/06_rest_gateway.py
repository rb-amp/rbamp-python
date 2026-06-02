"""
Example 6 — Flask REST gateway exposing rbAmp readings over HTTP.

Endpoints:
    GET  /                 -> human-readable status page
    GET  /api/v1/rt        -> real-time U/I/P/PF JSON
    GET  /api/v1/period    -> latched period snapshot + Wh totals
    POST /api/v1/latch     -> issue CMD_LATCH_PERIOD (rare; use period normally)
    GET  /api/v1/info      -> firmware / variant / topology

Dependencies:
    pip install flask

Run:
    python 06_rest_gateway.py
    curl http://localhost:5000/api/v1/rt
    curl http://localhost:5000/api/v1/period
"""

import threading

from flask import Flask, jsonify, render_template_string
from smbus2 import SMBus

from rbamp import RbAmp, RbAmpStaleError, topology_name


_DEV_LOCK = threading.Lock()  # smbus2 is not inherently thread-safe


def make_app(bus_no=1, addr=0x50):
    bus = SMBus(bus_no)
    dev = RbAmp(bus, addr)
    dev.begin()

    app = Flask(__name__)

    @app.route("/")
    def index():
        with _DEV_LOCK:
            s = dev.read_all()
        return render_template_string(
            """
            <h1>rbAmp 0x{{ addr }}</h1>
            <p>topology: {{ topo }} ({{ s.channels }} channels)</p>
            <ul>
              <li>U = {{ "%.1f"|format(s.voltage) }} V</li>
              <li>f = {{ "%.1f"|format(s.frequency) }} Hz</li>
              {% for ch in range(s.channels) %}
                <li>I{{ ch }} = {{ "%.2f"|format(s.current[ch]) }} A
                    P{{ ch }} = {{ "%.1f"|format(s.power[ch]) }} W
                    PF{{ ch }} = {{ "%+.2f"|format(s.power_factor[ch]) }}</li>
              {% endfor %}
            </ul>
            <p><a href="/api/v1/period">period snapshot (JSON)</a></p>
            """,
            addr="{:02X}".format(dev.address),
            topo=topology_name(s.topology),
            s=s,
        )

    @app.route("/api/v1/rt")
    def api_rt():
        with _DEV_LOCK:
            s = dev.read_all()
        return jsonify({
            "voltage_v": s.voltage,
            "voltage_peak_v": s.voltage_peak,
            "current_a": s.current[: s.channels],
            "current_peak_a": s.current_peak[: s.channels],
            "power_w": s.power[: s.channels],
            "power_factor": s.power_factor[: s.channels],
            "frequency_hz": s.frequency,
            "topology": topology_name(s.topology),
            "channels": s.channels,
        })

    @app.route("/api/v1/period")
    def api_period():
        try:
            with _DEV_LOCK:
                snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            return jsonify({"error": "stale snapshot"}), 503
        return jsonify({
            "avg_p_w": snap.avg_p[: dev.channels],
            "max_p_w": snap.max_p,
            "master_dt_ms": snap.master_dt_ms,
            "device_latch_ms": snap.latch_ms,
            "wh_total": [dev.energy.wh(ch) for ch in range(dev.channels)],
        })

    @app.route("/api/v1/latch", methods=["POST"])
    def api_latch():
        with _DEV_LOCK:
            dev.latch_period()
        return jsonify({"ok": True})

    @app.route("/api/v1/info")
    def api_info():
        return jsonify({
            "address": dev.address,
            "firmware_version": dev.firmware_version,
            "topology": topology_name(dev.topology),
            "channels": dev.channels,
            "has_voltage_hw": dev.has_voltage_hw,
        })

    return app


if __name__ == "__main__":
    app = make_app(bus_no=1, addr=0x50)
    app.run(host="0.0.0.0", port=5000, debug=False)
