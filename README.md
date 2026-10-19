# Ocypus Digital Cooler Windows Driver (Open Source)

A lightweight, clean, and 100% open-source Windows driver for **Ocypus Digital** CPU coolers (Iota A40, Delta A40, Gamma, etc.) with integrated LCD temperature displays.

[![Python Version](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-lightgrey.svg)]()

---

## Why This Project Exists

Ocypus coolers use an internal USB header to update their digital LED/LCD temperature displays.

* **Safety & Transparency:** The official vendor software is closed-source and proprietary, requiring users to install unaudited third-party binaries on their systems.
* **No Windows Alternative:** Existing open-source scripts were developed exclusively for Linux and do not work out of the box on Windows.
* **Lightweight & Clean:** Provides a transparent, low-overhead solution that runs quietly without bloat or heavy background suites.

---

## Key Features

* **Zero External Dependencies:** No `pip install` required! Uses native Windows APIs (`hid.dll` and `setupapi.dll` via Python's built-in `ctypes`).
* **Clean & Safe Sensor Architecture:** Reads temperatures directly from standard Windows shared memory exposed by trusted tools:
  * **[Core Temp](https://www.alcpu.com/CoreTemp/):** Reads `CoreTempMappingObject` with microsecond latency and zero overhead.
  * **[HWiNFO](https://www.hwinfo.com/):** Reads `Global\HWiNFO_SENS_SM2` shared memory.
  * **NVIDIA GPUs:** Native fallback via `nvidia-smi` (no admin required).
* **True Silent Background Mode:** Launches invisibly in the background using `pythonw.exe` (no black terminal windows popping up).
* **One-Click Windows Startup:** Built-in command to register automatic startup with Windows Task Scheduler upon user logon.

---

## Hardware Protocol Details

For hardware hackers and developers, here is the reverse-engineered USB HID protocol:

* **Vendor ID (VID):** `0x1A2C` (China Resource Semico)
* **Product ID (PID):** `0x434D`
* **Target HID Collection:** `UsagePage = 0xFF01`, `Usage = 0x0001` (Collection 7, Feature Report Length = 64/65 bytes)
* **Packet Structure (65-byte Feature Report):**

| Byte Offset | Value | Description |
| :---: | :---: | :--- |
| `[0]` | `0x07` | Report ID |
| `[1]` | `0xFF` | Display Update Opcode |
| `[2]` | `0x00` or `0x01` | Unit Flag (`0x00` = Celsius °C, `0x01` = Fahrenheit °F) |
| `[3]` | `temp // 100` | Hundreds digit (e.g., `0` or `1`) |
| `[4]` | `(temp // 10) % 10` | Tens digit (e.g., `0` to `9`) |
| `[5]` | `temp % 10` | Units digit (e.g., `0` to `9`) |
| `[6..64]` | `0x00` | Reserved / Zero padding |

*To blank/turn off the screen, send `0x00` in bytes `[3]`, `[4]`, and `[5]`.*

---

## Installation & Quick Start

### Prerequisites
1. **Python 3.8+** installed on Windows ([python.org](https://www.python.org/downloads/)). Make sure *"Add python.exe to PATH"* was checked during installation.
2. **Temperature Provider (Recommended: Core Temp):**
   * Install Core Temp via winget:
     ```powershell
     winget install ALCPU.CoreTemp
     ```
   * Open Core Temp once, go to **Options ➔ Settings ➔ General**, and check **"Enable shared memory"**.
   * *(Optional)*: Check **"Start Core Temp with Windows"** and **"Start minimized"**.

### Download
Clone this repository or download `ocypus_driver.py`:
```powershell
git clone https://github.com/your-username/ocypus-digital-driver.git
cd ocypus-digital-driver
```

---

## Usage

### 1. Check Connected Devices & Sensors
```powershell
python ocypus_driver.py list
```
Outputs the active HID collections and currently detected temperatures:
```text
=== Ocypus HID Collections (VID_1A2C & PID_434D): 8 found ===
  UsagePage=0x0001 | Usage=0x0006 | FeatLen= 0 | RW=False
  UsagePage=0x000C | Usage=0x0001 | FeatLen= 0 | RW=True
  UsagePage=0xFF01 | Usage=0x0001 | FeatLen=64 | RW=True  <-- [COOLER LCD PANEL]

=== Active Temperature Sensors ===
  CoreTemp:AMD Ryzen 7 7700X: 47.0°C
  NVIDIA:GPU Temperature: 38.0°C
```

### 2. Run in Foreground (Testing)
```powershell
# Auto-detects CPU temperature (Celsius):
python ocypus_driver.py on

# Display in Fahrenheit:
python ocypus_driver.py on -u f

# Display GPU temperature instead:
python ocypus_driver.py on -s gpu
```

### 3. Run Silently in Background
To run completely in the background without any command prompt window:
```powershell
python ocypus_driver.py start
```

To check if the background driver is running:
```powershell
python ocypus_driver.py status
```

To stop the background driver and turn off the screen:
```powershell
python ocypus_driver.py stop
```

---

## Automatic Startup with Windows

To have the driver start invisibly in the background whenever you log in to Windows:

```powershell
python ocypus_driver.py install-startup
```

If you ever want to remove it from Windows startup:
```powershell
python ocypus_driver.py uninstall-startup
```

---

## Command Reference

| Command | Description |
| :--- | :--- |
| `python ocypus_driver.py start` | Starts the driver silently in background using `pythonw.exe` |
| `python ocypus_driver.py stop` | Stops the background driver and blanks the screen |
| `python ocypus_driver.py status` | Checks if the background driver is active (displays PID) |
| `python ocypus_driver.py install-startup` | Registers silent auto-start on Windows login and disables USB Selective Suspend |
| `python ocypus_driver.py uninstall-startup` | Unregisters the auto-start entry |
| `python ocypus_driver.py reset-usb` | Resets the cooler's USB port if the hardware endpoint stalls (Win32 Error 31) |
| `python ocypus_driver.py list` | Lists all detected HID collections and active sensors |
| `python ocypus_driver.py on` | Runs in foreground with real-time console telemetry |
| `python ocypus_driver.py off` | Blanks the cooler screen |
| `python ocypus_driver.py test -t 88` | Sends a fixed test number (`88`) to verify screen communication |

---

## License

This project is licensed under the [MIT License](LICENSE).
Feel free to use, modify, and contribute!
