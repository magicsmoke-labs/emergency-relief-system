from __future__ import annotations

import sqlite3
import sys

DB_FILE = "relief_system.db"


def seed_database() -> None:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    print(f"[*] Initializing schema on '{DB_FILE}'...")

    cursor.executescript("""
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

    # Clear existing state for a fresh demo run
    print("[*] Flushing previous logs, requests, inventory, and shelters...")
    cursor.execute("DELETE FROM allocation_logs;")
    cursor.execute("DELETE FROM requests;")
    cursor.execute("DELETE FROM inventory;")
    cursor.execute("DELETE FROM shelters;")

    # -----------------------------
    # 1. SHELTERS
    # -----------------------------
    shelters_data = [
        ("SH-NORTH", "Northern Evacuation Complex", 5, 25.276987, 55.296249),
        ("SH-METRO", "Central Metro Relief Hub", 4, 25.204849, 55.270783),
        ("SH-COAST", "West Harbor Sports Arena", 4, 25.185201, 55.241021),
        ("SH-SOUTH", "South District Community School", 2, 25.112440, 55.201450),
        ("SH-RIDGE", "East Desert Forward Outpost", 1, 25.075420, 55.334180),
    ]

    cursor.executemany(
        "INSERT INTO shelters (shelter_id, name, priority, lat, lon) VALUES (?, ?, ?, ?, ?);",
        shelters_data
    )
    print(f" [+] Inserted {len(shelters_data)} shelters.")

    # -----------------------------
    # 2. WAREHOUSE INVENTORY
    # -----------------------------
    inventory_data = [
        # Main Central Logistics Depot (Large supplies)
        ("WH-CENTRAL", "water", 1200),
        ("WH-CENTRAL", "blankets", 600),
        ("WH-CENTRAL", "mre", 1500),
        ("WH-CENTRAL", "first aid kit", 250),
        ("WH-CENTRAL", "tents", 120),
        ("WH-CENTRAL", "generators", 15),

        # Port Customs Warehouse (Heavy gear & rations)
        ("WH-PORT", "water", 400),
        ("WH-PORT", "mre", 800),
        ("WH-PORT", "generators", 30),
        ("WH-PORT", "tents", 200),

        # Airfield Forward Base (Fast medical & rations)
        ("WH-AIRFIELD", "first aid kit", 300),
        ("WH-AIRFIELD", "blankets", 250),
        ("WH-AIRFIELD", "water", 350),

        # Regional Staging Cache (Limited reserve)
        ("WH-SOUTH", "water", 100),
        ("WH-SOUTH", "blankets", 80),
        ("WH-SOUTH", "mre", 150),
    ]

    cursor.executemany(
        "INSERT INTO inventory (warehouse_id, item, quantity) VALUES (?, ?, ?);",
        inventory_data
    )
    print(f" [+] Inserted {len(inventory_data)} warehouse inventory records.")

    # -----------------------------
    # 3. SAMPLE RELIEF REQUESTS
    # -----------------------------
    requests_data = [
        # Critical requests (Higher shelter priority should win ties)
        ("REQ-001", "SH-NORTH", "water", 500, "Critical"),
        ("REQ-002", "SH-METRO", "first aid kit", 120, "Critical"),
        ("REQ-003", "SH-COAST", "generators", 25, "Critical"),

        # High priority requests
        ("REQ-004", "SH-NORTH", "blankets", 400, "High"),
        ("REQ-005", "SH-METRO", "mre", 600, "High"),
        ("REQ-006", "SH-SOUTH", "water", 300, "High"),

        # Moderate requests (some will consume leftover stock)
        ("REQ-007", "SH-COAST", "tents", 150, "Moderate"),
        ("REQ-008", "SH-SOUTH", "blankets", 100, "Moderate"),
        ("REQ-009", "SH-RIDGE", "water", 250, "Moderate"),

        # Stress-test request: Intentionally demands more than total stock to test PARTIAL status
        ("REQ-010", "SH-RIDGE", "generators", 100, "Moderate"),
    ]

    cursor.executemany(
        """
        INSERT INTO requests (request_id, shelter_id, item, quantity, urgency, status, fulfilled_qty)
        VALUES (?, ?, ?, ?, ?, 'PENDING', 0);
        """,
        requests_data
    )
    print(f" [+] Inserted {len(requests_data)} mock requests.")

    conn.commit()
    conn.close()
    print("\n[SUCCESS] Mock data populated cleanly. You are ready to run tests or start Flask.")


if __name__ == "__main__":
    try:
        seed_database()
    except sqlite3.Error as e:
        print(f"[ERROR] Database seeding failed: {e}", file=sys.stderr)
        sys.exit(1)