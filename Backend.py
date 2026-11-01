from __future__ import annotations

import sqlite3
from typing import Any
lazy import csv
lazy import io
lazy import uuid
from flask import Flask, request, jsonify, Response
from flask import render_template

type JsonDict = dict[str, Any]
type UrgencyLevel = str

DB_FILE = "relief_system.db"
app = Flask(__name__)

# DATABASE & SCHEMA

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shelters (
                shelter_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 5),
                lat REAL NOT NULL,
                lon REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory (
                warehouse_id TEXT NOT NULL,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity >= 0),
                PRIMARY KEY (warehouse_id, item)
            );

            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY,
                shelter_id TEXT NOT NULL,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                urgency TEXT NOT NULL CHECK (urgency IN ('Critical', 'High', 'Moderate')),
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PARTIAL', 'FULFILLED', 'UNFULFILLED')),
                fulfilled_qty INTEGER NOT NULL DEFAULT 0 CHECK (fulfilled_qty >= 0),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shelter_id) REFERENCES shelters (shelter_id)
            );

            CREATE TABLE IF NOT EXISTS allocation_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                warehouse_id TEXT NOT NULL,
                item TEXT NOT NULL,
                allocated_qty INTEGER NOT NULL CHECK (allocated_qty > 0),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES requests (request_id)
            );
        """)

# SHELTERS & INVENTORY

@app.route("/api/shelters", methods=["GET", "POST"])
def handle_shelters():
    with get_db() as conn:
        cursor = conn.cursor()
        if request.method == "POST":
            data: JsonDict = request.get_json(force=True)
            shelter_id = data.get("shelter_id") or str(uuid.uuid4())[:8]

            try:
                cursor.execute(
                    "INSERT INTO shelters (shelter_id, name, priority, lat, lon) VALUES (?, ?, ?, ?, ?)",
                    (shelter_id, data["name"], int(data["priority"]), float(data["lat"]), float(data["lon"]))
                )
                return jsonify({"status": "created", "shelter_id": shelter_id}), 201
            except sqlite3.Error as e:
                return jsonify({"error": str(e)}), 400

        cursor.execute("SELECT * FROM shelters")
        return jsonify([dict(row) for row in cursor.fetchall()]), 200

@app.route("/api/inventory", methods=["GET", "POST"])
def handle_inventory():
    with get_db() as conn:
        cursor = conn.cursor()
        if request.method == "POST":
            data: JsonDict = request.get_json(force=True)
            warehouse_id = data["warehouse_id"]
            item = data["item"].strip().lower()
            quantity = int(data["quantity"])

            try:
                cursor.execute("""
                    INSERT INTO inventory (warehouse_id, item, quantity)
                    VALUES (?, ?, ?)
                    ON CONFLICT(warehouse_id, item) DO UPDATE SET quantity = quantity + excluded.quantity
                """, (warehouse_id, item, quantity))
                return jsonify({"status": "inventory updated", "warehouse_id": warehouse_id, "item": item}), 200
            except sqlite3.IntegrityError as e:
                return jsonify({"error": "Operation violates non-negative inventory constraint", "details": str(e)}), 400

        cursor.execute("SELECT warehouse_id, item, quantity FROM inventory ORDER BY warehouse_id, item")
        return jsonify([dict(row) for row in cursor.fetchall()]), 200

# REQUESTS

@app.route("/api/requests", methods=["GET", "POST"])
def handle_requests():
    with get_db() as conn:
        cursor = conn.cursor()
        if request.method == "POST":
            data: JsonDict = request.get_json(force=True)
            urgency = data["urgency"].strip().capitalize()

            match urgency:
                case "Critical" | "High" | "Moderate":
                    pass
                case _:
                    return jsonify({"error": f"Invalid urgency '{urgency}'. Must be Critical, High, or Moderate"}), 400

            request_id = data.get("request_id") or f"REQ-{uuid.uuid4().hex[:6].upper()}"

            try:
                cursor.execute("""
                    INSERT INTO requests (request_id, shelter_id, item, quantity, urgency, status, fulfilled_qty)
                    VALUES (?, ?, ?, ?, ?, 'PENDING', 0)
                """, (request_id, data["shelter_id"], data["item"].strip().lower(), int(data["quantity"]), urgency))
                return jsonify({"status": "submitted", "request_id": request_id}), 201
            except sqlite3.Error as e:
                return jsonify({"error": str(e)}), 400

        cursor.execute("""
            SELECT r.*, s.name AS shelter_name, s.priority AS shelter_priority
            FROM requests r
            JOIN shelters s ON r.shelter_id = s.shelter_id
            ORDER BY r.created_at DESC
        """)
        return jsonify([dict(row) for row in cursor.fetchall()]), 200

# ALLOCATION ENGINE

@app.route("/api/allocate", methods=["POST"])
def run_allocation():
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT r.request_id, r.shelter_id, r.item, r.quantity, r.fulfilled_qty, r.urgency,
                   s.priority AS shelter_priority,
                   CASE r.urgency
                       WHEN 'Critical' THEN 3
                       WHEN 'High'     THEN 2
                       WHEN 'Moderate' THEN 1
                       ELSE 0
                   END AS urgency_rank
            FROM requests r
            JOIN shelters s ON r.shelter_id = s.shelter_id
            WHERE r.status IN ('PENDING', 'PARTIAL')
            ORDER BY urgency_rank DESC, s.priority DESC, r.created_at ASC
        """)
        pending = [dict(row) for row in cursor.fetchall()]
        allocations_made: list[JsonDict] = []

        try:
            for req in pending:
                needed = req["quantity"] - req["fulfilled_qty"]
                if needed <= 0:
                    continue

                cursor.execute("""
                    SELECT warehouse_id, quantity
                    FROM inventory
                    WHERE item = ? AND quantity > 0
                    ORDER BY quantity DESC
                """, (req["item"],))
                stock_sources = [dict(row) for row in cursor.fetchall()]

                fulfilled_this_batch = 0

                for stock in stock_sources:
                    if needed <= 0:
                        break

                    wh_id = stock["warehouse_id"]
                    take = min(stock["quantity"], needed)

                    cursor.execute("""
                        UPDATE inventory
                        SET quantity = quantity - ?
                        WHERE warehouse_id = ? AND item = ?
                    """, (take, wh_id, req["item"]))

                    cursor.execute("""
                        INSERT INTO allocation_logs (request_id, warehouse_id, item, allocated_qty)
                        VALUES (?, ?, ?, ?)
                    """, (req["request_id"], wh_id, req["item"], take))

                    needed -= take
                    fulfilled_this_batch += take
                    allocations_made.append({
                        "request_id": req["request_id"],
                        "warehouse_id": wh_id,
                        "item": req["item"],
                        "allocated_qty": take
                    })

                total = req["fulfilled_qty"] + fulfilled_this_batch
                match total:
                    case t if t == req["quantity"]:
                        new_status = "FULFILLED"
                    case t if t > 0:
                        new_status = "PARTIAL"
                    case _:
                        new_status = "UNFULFILLED"

                cursor.execute("""
                    UPDATE requests
                    SET fulfilled_qty = ?, status = ?
                    WHERE request_id = ?
                """, (total, new_status, req["request_id"]))

            return jsonify({
                "status": "allocation complete",
                "allocations_count": len(allocations_made),
                "allocations": allocations_made
            }), 200

        except sqlite3.IntegrityError as err:
            conn.rollback()
            return jsonify({"error": "Aborted: Negative stock constraint tripped", "details": str(err)}), 500

# AUDIT LOGS & REPORTING

@app.route("/api/logs", methods=["GET"])
def get_logs():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT l.log_id, l.request_id, l.warehouse_id, l.item, l.allocated_qty, l.timestamp,
                   s.name AS shelter_name, r.urgency
            FROM allocation_logs l
            JOIN requests r ON l.request_id = r.request_id
            JOIN shelters s ON r.shelter_id = s.shelter_id
            ORDER BY l.timestamp DESC
        """)
        return jsonify([dict(row) for row in cursor.fetchall()]), 200

@app.route("/api/export/<format_type>", methods=["GET"])
def export_report(format_type: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                r.request_id,
                s.name AS shelter_name,
                s.priority AS shelter_priority,
                r.item,
                r.quantity AS requested_qty,
                r.fulfilled_qty,
                (r.quantity - r.fulfilled_qty) AS unfulfilled_qty,
                r.urgency,
                r.status,
                r.created_at
            FROM requests r
            JOIN shelters s ON r.shelter_id = s.shelter_id
            ORDER BY r.created_at ASC
        """)
        rows = [dict(row) for row in cursor.fetchall()]

    match format_type.lower():
        case "json":
            return jsonify(rows), 200

        case "csv":
            buf = io.StringIO()
            if rows:
                writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            else:
                buf.write("No requests found\n")

            return Response(
                buf.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment;filename=relief_summary.csv"}
            )

        case _:
            return jsonify({"error": "Unsupported format. Use 'json' or 'csv'"}), 400

@app.route("/")
def index():
    return render_template("index.html")

# RUNTIME INITIALIZATION

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)