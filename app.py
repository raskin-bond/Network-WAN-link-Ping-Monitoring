#!/usr/bin/env python3

from flask import Flask, jsonify, request, redirect, send_file, render_template_string
import subprocess
import threading
import time
import platform
from datetime import datetime
import pandas as pd
import os
import openpyxl

app = Flask(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

PING_INTERVAL = 5          # Seconds between polling cycles
PING_COUNT = 3             # Pings per device
LOSS_THRESHOLD = 1         # Tolerated failures
IPS_RELOAD_INTERVAL = 300  # Reload ips.txt every 5 minutes

IPS_FILE = "ips.txt"
EXCEL_FILE = "device_status.xlsx"


# ============================================================
# AUTOMATIC REPORT CONFIGURATION
# ============================================================

# 23:50 = 11:50 PM
REPORT_HOUR = 16
REPORT_MINUTE = 44

# Folder where automatic reports are stored
REPORT_DIR = "reports"


# ============================================================
# GLOBAL DATA
# ============================================================

devices = {}

devices_lock = threading.Lock()

# Separate lock for event log/report generation
log_lock = threading.Lock()

full_log = []


# ============================================================
# CREATE REPORT DIRECTORY
# ============================================================

def create_report_directory():

    if not os.path.exists(REPORT_DIR):

        os.makedirs(REPORT_DIR)

        print(
            f"[REPORT] Created report directory: "
            f"{os.path.abspath(REPORT_DIR)}"
        )


# ============================================================
# UPDATE EXISTING IPS
# ============================================================

def update_ips_file_with_short_name():

    if not os.path.exists(IPS_FILE):
        return

    lines = []
    updated = False

    with open(IPS_FILE) as f:

        for line in f:

            if not line.strip():
                continue

            parts = line.strip().split(",")

            while len(parts) < 4:

                parts.append("")
                updated = True

            lines.append(",".join(parts))

    if updated:

        with open(IPS_FILE, "w") as f:

            f.write("\n".join(lines))


# ============================================================
# LOAD IPS
# ============================================================

def load_ips():

    devs = {}

    if not os.path.exists(IPS_FILE):
        return devs

    with open(IPS_FILE) as f:

        for line in f:

            if not line.strip() or line.startswith("#"):
                continue

            ip, name, group, short = (
                line.strip().split(",")
                + ["", "", "", ""]
            )[:4]

            devs[ip] = {

                "name": name or ip,

                "group": group or "Default",

                "short_name": short,

                "status": "down",

                "latency": "N/A",

                "history": [],

                "last_logged_status": None,

                "is_loss": False,

                "loss_since": None
            }

    return devs


# ============================================================
# AUTO RELOAD IPS
# ============================================================

def ips_reload_loop():

    global devices

    while True:

        time.sleep(IPS_RELOAD_INTERVAL)

        file_devices = load_ips()

        file_ips = set(file_devices.keys())

        with devices_lock:

            current_ips = set(devices.keys())

            # ------------------------
            # ADD NEW IPS
            # ------------------------

            for ip in file_ips - current_ips:

                devices[ip] = file_devices[ip]

                print(
                    f"[AUTO-RELOAD] Added: {ip}"
                )

            # ------------------------
            # REMOVE DELETED IPS
            # ------------------------

            for ip in current_ips - file_ips:

                devices.pop(ip, None)

                print(
                    f"[AUTO-RELOAD] Removed: {ip}"
                )

        print(
            "[AUTO-RELOAD] Sync completed."
        )


# ============================================================
# SAVE IP
# ============================================================

def save_ip(ip, name, group, short):

    with open(IPS_FILE, "a") as f:

        f.write(
            f"{ip},{name},{group},{short}\n"
        )


# ============================================================
# MULTI PING
# ============================================================

def ping_device(ip):

    success = 0

    latencies = []

    for _ in range(PING_COUNT):

        if platform.system().lower() == "windows":

            cmd = [
                "ping",
                "-n",
                "1",
                "-w",
                "1000",
                ip
            ]

        else:

            cmd = [
                "ping",
                "-c",
                "1",
                "-W",
                "1",
                ip
            ]

        start = time.time()

        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL
        )

        latency = round(
            (time.time() - start) * 1000,
            1
        )

        if result.returncode == 0:

            success += 1

            latencies.append(latency)

    if latencies:

        avg_latency = round(
            sum(latencies) / len(latencies),
            1
        )

    else:

        avg_latency = "N/A"

    if success == 0:

        return "down", "N/A"

    elif success <= LOSS_THRESHOLD:

        return "loss", avg_latency

    else:

        return "up", avg_latency


# ============================================================
# POLLING LOOP
# ============================================================

def polling_loop():

    while True:

        with devices_lock:

            ips_list = list(devices.keys())

        for ip in ips_list:

            with devices_lock:

                d = devices.get(ip)

                if not d:
                    continue

            status_now, latency = ping_device(ip)

            now = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            with devices_lock:

                # ------------------------
                # STATUS HISTORY
                # ------------------------

                if (
                    not d["history"]
                    or
                    d["history"][-1]["status"]
                    != status_now
                ):

                    d["history"].append({

                        "status": status_now,

                        "time": now

                    })

                    if len(d["history"]) > 3:

                        d["history"].pop(0)

                # ------------------------
                # PACKET LOSS
                # ------------------------

                if status_now == "loss":

                    if not d["is_loss"]:

                        d["loss_since"] = now

                    d["is_loss"] = True

                else:

                    d["is_loss"] = False

                    d["loss_since"] = None

                # ------------------------
                # LOG STATUS CHANGE
                # ------------------------

                if (
                    d["last_logged_status"]
                    != status_now
                ):

                    event = {

                        "IP": ip,

                        "Name": d["name"],

                        "Group": d["group"],

                        "Status": status_now,

                        "Time": now
                    }

                    with log_lock:

                        full_log.append(event)

                    d["last_logged_status"] = (
                        status_now
                    )

                d["status"] = status_now

                d["latency"] = latency

        time.sleep(PING_INTERVAL)


# ============================================================
# GENERATE EXCEL REPORT
# ============================================================

def generate_excel_report(
    automatic=False,
    report_date=None
):

    create_report_directory()

    with log_lock:

        log_copy = list(full_log)

    # ------------------------------------------------
    # Automatic report = only today's events
    # ------------------------------------------------

    if automatic:

        if report_date is None:

            report_date = (
                datetime.now()
                .strftime("%Y-%m-%d")
            )

        daily_log = []

        for event in log_copy:

            event_date = (
                event["Time"]
                .split(" ")[0]
            )

            if event_date == report_date:

                daily_log.append(event)

        report_data = daily_log

    else:

        report_data = log_copy

    if not report_data:

        print(
            "[REPORT] No event data available."
        )

        return None

    df = pd.DataFrame(report_data)

    # ------------------------------------------------
    # FILE NAME
    # ------------------------------------------------

    if automatic:

        filename = (
            f"device_status_"
            f"{report_date}.xlsx"
        )

        filepath = os.path.join(
            REPORT_DIR,
            filename
        )

    else:

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        filename = (
            f"device_status_"
            f"{timestamp}.xlsx"
        )

        filepath = os.path.join(
            "/tmp",
            filename
        )

    # ------------------------------------------------
    # CREATE EXCEL
    # ------------------------------------------------

    df.to_excel(
        filepath,
        index=False
    )

    # ------------------------------------------------
    # FORMAT EXCEL
    # ------------------------------------------------

    wb = openpyxl.load_workbook(filepath)

    ws = wb.active

    ws.title = "Network Events"

    # Column width
    for col in ws.columns:

        ws.column_dimensions[
            col[0].column_letter
        ].width = 22

    # Freeze header
    ws.freeze_panes = "A2"

    # Enable filter
    ws.auto_filter.ref = ws.dimensions

    wb.save(filepath)

    print(
        f"[REPORT] Generated: "
        f"{os.path.abspath(filepath)}"
    )

    return filepath


# ============================================================
# AUTOMATIC DAILY REPORT THREAD
# ============================================================

def automatic_report_loop():

    print(
        f"[REPORT] Automatic report scheduled "
        f"daily at "
        f"{REPORT_HOUR:02d}:"
        f"{REPORT_MINUTE:02d}"
    )

    last_report_date = None

    while True:

        now = datetime.now()

        today = now.strftime(
            "%Y-%m-%d"
        )

        # --------------------------------------------
        # Check configured report time
        # --------------------------------------------

        if (
            now.hour == REPORT_HOUR
            and
            now.minute == REPORT_MINUTE
            and
            last_report_date != today
        ):

            print(
                f"[REPORT] Starting automatic "
                f"daily report for {today}"
            )

            try:

                filepath = generate_excel_report(
                    automatic=True,
                    report_date=today
                )

                if filepath:

                    print(
                        "[REPORT] Daily report "
                        "completed successfully."
                    )

                else:

                    print(
                        "[REPORT] No events today. "
                        "Report not generated."
                    )

            except Exception as e:

                print(
                    f"[REPORT ERROR] {e}"
                )

            # Prevent multiple reports
            # during the same minute
            last_report_date = today

        # Check every 20 seconds
        time.sleep(20)


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():

    return render_template_string(
        PAGE_HTML
    )


# ============================================================
# STATUS API
# ============================================================

@app.route("/status")
def status():

    with devices_lock:

        return jsonify(devices)


# ============================================================
# EVENTS API
# ============================================================

@app.route("/events")
def events():

    with log_lock:

        events_copy = (
            full_log[-100:][::-1]
        )

    return jsonify(events_copy)


# ============================================================
# ADD DEVICE
# ============================================================

@app.route(
    "/add",
    methods=["POST"]
)
def add():

    ip = request.form["ip"].strip()

    with devices_lock:

        if ip in devices:

            return redirect("/")

    status_now, latency = (
        ping_device(ip)
    )

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    new_device = {

        "name":
            request.form.get(
                "name",
                ip
            ),

        "group":
            request.form.get(
                "group",
                "Default"
            ),

        "short_name":
            request.form.get(
                "short_name",
                ""
            ),

        "status":
            status_now,

        "latency":
            latency,

        "history": [
            {
                "status":
                    status_now,

                "time":
                    now
            }
        ],

        "last_logged_status":
            status_now,

        "is_loss":
            status_now == "loss",

        "loss_since":
            now
            if status_now == "loss"
            else None
    }

    with devices_lock:

        devices[ip] = new_device

    event = {

        "IP": ip,

        "Name":
            new_device["name"],

        "Group":
            new_device["group"],

        "Status":
            status_now,

        "Time":
            now
    }

    with log_lock:

        full_log.append(event)

    save_ip(
        ip,
        new_device["name"],
        new_device["group"],
        new_device["short_name"]
    )

    return redirect("/")


# ============================================================
# MANUAL EXPORT
# ============================================================

@app.route("/export")
def export():

    filepath = generate_excel_report(
        automatic=False
    )

    if not filepath:

        return (
            "No data to export",
            400
        )

    return send_file(
        filepath,
        as_attachment=True
    )


# ============================================================
# HTML
# ============================================================

PAGE_HTML = """
<!DOCTYPE html>
<html>

<head>

<title>IP Ping Monitor</title>

<style>

body {

    font-family:Arial;

    margin:20px;

    background-image:
    url('{{ url_for('static',
    filename='bg1.jpg') }}');

    background-size:cover;

    background-attachment:fixed;
}


h2 {

    color:white;

    text-shadow:
    1px 1px 2px #000;
}


.grid {

    display:flex;

    flex-wrap:wrap;

    gap:6px;
}


.box {

    width:38px;

    height:38px;

    border-radius:4px;

    cursor:pointer;

    opacity:0.85;
}


.up {

    background:#4caf50;
}


.down {

    background:#f44336;

    animation:
    blink 1s infinite;
}


.loss {

    background:#ffeb3b;
}


@keyframes blink {

    50% {

        opacity:0.3;

    }

}


.tooltip {

    position:fixed;

    background:
    rgba(255,255,255,0.95);

    border-radius:6px;

    padding:8px 10px;

    font-size:13px;

    box-shadow:
    0 4px 12px
    rgba(0,0,0,0.25);

    display:none;

    z-index:1000;

    pointer-events:none;
}


.group {

    margin-bottom:20px;

    color:white;
}


/* =================================================
   ROLLUP BANNER
================================================= */

#rollup {

    position:fixed;

    bottom:20px;

    right:20px;

    width:320px;

    height:170px;

    background:
    rgba(0,0,0,0.45);

    backdrop-filter:
    blur(8px);

    color:
    rgba(255,255,255,0.95);

    border-radius:14px;

    overflow:hidden;

    transition:
    all 0.35s ease;

    z-index:999;

    font-size:12px;

    border:
    1px solid
    rgba(255,255,255,0.15);
}


#rollup:hover {

    width:540px;

    height:360px;
}


#rollup-header {

    padding:10px 14px;

    font-weight:bold;

    text-transform:uppercase;

    border-bottom:
    1px solid
    rgba(255,255,255,0.18);
}


#rollup-body {

    padding:10px 12px;

    max-height:210px;

    overflow-y:auto;
}


.event {

    margin-bottom:6px;

    border-bottom:
    1px dashed
    rgba(255,255,255,0.2);
}


.up-event {

    color:#7CFC00;
}


.down-event {

    color:#ff6b6b;
}


.loss-event {

    color:#ffeb3b;
}


#rollup form {

    padding:8px 12px;

    border-top:
    1px solid
    rgba(255,255,255,0.18);
}


#rollup input {

    width:90px;

    margin:2px;

    font-size:11px;

    background:
    rgba(255,255,255,0.15);

    border:
    1px solid
    rgba(255,255,255,0.25);

    color:white;
}


#rollup button {

    background:
    rgba(255,255,255,0.2);

    border:
    1px solid
    rgba(255,255,255,0.3);

    color:white;

    cursor:pointer;
}


.export-btn {

    width:100%;

    margin-top:4px;
}

</style>

</head>


<body>


<h2>
Network Monitor
</h2>


<div id="main">
</div>


<div
id="tooltip"
class="tooltip">
</div>


<div id="rollup">

<div id="rollup-header">

Live Status Changes

</div>


<div id="rollup-body">
</div>


<form
method="post"
action="/add">

<input
name="ip"
placeholder="IP"
required>

<input
name="name"
placeholder="Name">

<input
name="group"
placeholder="Group">

<input
name="short_name"
placeholder="Short">

<button>
Add
</button>

</form>


<form
method="get"
action="/export">

<button
class="export-btn">

Export Excel

</button>

</form>

</div>


<script>

const tooltip =
document.getElementById(
    "tooltip"
);


function showTip(
    e,
    ip,
    name,
    group,
    status,
    latency,
    history_json,
    loss_since
) {

    tooltip.style.display =
    "block";

    let x =
    e.clientX + 15;

    let y =
    e.clientY + 15;


    if (
        x +
        tooltip.offsetWidth >
        window.innerWidth
    )

        x =
        e.clientX -
        tooltip.offsetWidth -
        15;


    if (
        y +
        tooltip.offsetHeight >
        window.innerHeight
    )

        y =
        e.clientY -
        tooltip.offsetHeight -
        15;


    tooltip.style.left =
    x + "px";

    tooltip.style.top =
    y + "px";


    let h =
    JSON.parse(
        decodeURIComponent(
            history_json
        )
    );


    tooltip.innerHTML =

    `<b>${name}</b>

    <div>
    IP:${ip}
    </div>

    <div>
    Status:${status.toUpperCase()}
    </div>

    <div>
    Latency:${latency} ms
    </div>

    ${
        status=="loss"
        ?
        `<div>
        Loss since:
        ${loss_since}
        </div>`
        :
        ""
    }

    ${
        h.map(
            x =>
            `<div>
            ${x.time}
            -
            ${x.status}
            </div>`
        ).join("")
    }`;

}


function hideTip() {

    tooltip.style.display =
    "none";

}


function load() {

fetch("/status")

.then(
    r => r.json()
)

.then(
d => {

    let g = {};

    let html = "";


    for (
        let ip in d
    ) {

        g[
            d[ip].group
        ] =
        g[
            d[ip].group
        ] || [];


        g[
            d[ip].group
        ].push(
            {
                ip:ip,
                ...d[ip]
            }
        );

    }


    for (
        let k in g
    ) {

        html +=

        `<div
        class="group">

        <b>${k}</b>

        <div
        class="grid">`;


        g[k].forEach(
        x => {

            let cls =
            x.status=="loss"
            ?
            "loss"
            :
            x.status;


            let hist =
            encodeURIComponent(
                JSON.stringify(
                    x.history
                )
            );


            html +=

            `<div>

            <div
            style="
            font-size:11px;
            color:white">

            ${
                x.short_name
                ||
                ""
            }

            </div>


            <div
            class="box ${cls}"

            onmousemove="
            showTip(
            event,
            '${x.ip}',
            '${x.name}',
            '${x.group}',
            '${x.status}',
            '${x.latency}',
            '${hist}',
            '${x.loss_since||""}'
            )"

            onmouseleave="
            hideTip()">

            </div>

            </div>`;

        });


        html +=

        "</div></div>";

    }


    document
    .getElementById(
        "main"
    )
    .innerHTML =
    html;

});

}


function loadEvents() {

fetch("/events")

.then(
    r => r.json()
)

.then(
e => {

    let h = "";


    e.forEach(
    x => {

        let c =
        x.Status=="loss"
        ?
        "loss-event"
        :
        x.Status=="down"
        ?
        "down-event"
        :
        "up-event";


        let a =
        x.Status=="loss"
        ?
        "⚠"
        :
        x.Status=="down"
        ?
        "⬇"
        :
        "⬆";


        h +=

        `<div
        class="event ${c}">

        <b>
        ${x.Name}
        </b>

        ${a}

        ${x.Status.toUpperCase()}

        <br>

        <small>
        ${x.Time}
        </small>

        </div>`;

    });


    document
    .getElementById(
        "rollup-body"
    )
    .innerHTML =
    h;

});

}


load();

loadEvents();


setInterval(
    load,
    10000
);


setInterval(
    loadEvents,
    5000
);

</script>


</body>

</html>
"""


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":

    # Make sure report directory exists
    create_report_directory()

    # Update old ips.txt format
    update_ips_file_with_short_name()

    # Load devices
    devices = load_ips()

    print(
        f"[START] Loaded "
        f"{len(devices)} devices."
    )

    print(
        f"[REPORT] Daily report time: "
        f"{REPORT_HOUR:02d}:"
        f"{REPORT_MINUTE:02d}"
    )

    print(
        f"[REPORT] Report directory: "
        f"{os.path.abspath(REPORT_DIR)}"
    )

    # Start monitoring
    threading.Thread(
        target=polling_loop,
        daemon=True
    ).start()

    # Start ips.txt auto reload
    threading.Thread(
        target=ips_reload_loop,
        daemon=True
    ).start()

    # Start automatic report scheduler
    threading.Thread(
        target=automatic_report_loop,
        daemon=True
    ).start()

    # Start Flask
    app.run(
        host="0.0.0.0",
        port=8080,
        threaded=True
    )
