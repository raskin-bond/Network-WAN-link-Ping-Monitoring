# 📡 Simple Network Ping Monitor (Flask)

A lightweight, web-based network monitoring tool that continuously checks IP reachability using ICMP ping and displays live status in a clean dashboard.  
Designed for labs, NOCs, and small-to-medium environments where simplicity and visibility matter.

---

## 🚀 Features

- ✅ Real-time IP reachability monitoring
- 🌐 Web-based dashboard (Flask)
- 🟢 Green tiles for **UP** devices
- 🔴 Red blinking tiles for **DOWN** devices
- 🔄 Automatic refresh (no manual reload)
- 📦 Group-based device organization
- 🏷️ Device names and short labels
- 🕒 Status change history (flap tracking)
- ⏱️ Ping latency display
- 📤 Export status history to Excel (`.xlsx`)
- ➕ Add devices dynamically from the UI
- 💾 Flat-file storage (no database required)
- 🪶 Lightweight and low resource usage
- 🔒 Read-only monitoring (ICMP only)

---

## 🛠️ Tech Stack

- Python 3.x
- Flask
- HTML / CSS / JavaScript
- pandas
- openpyxl

---

## 📂 Project Structure

├── app.py # Main Flask application
├── ips.txt # Device list (auto-created)
├── device_status.xlsx # Exported status history
├── static/
│ └── bg1.jpg # Optional background image
├── README.md


