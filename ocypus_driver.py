#!/usr/bin/env python3
"""
ocypus_driver.py
---------------------------------
Open-Source Windows Driver for Ocypus Digital Coolers (Iota A40 / Delta A40 / Gamma).

Features:
  • Pure Windows HID communication using native Windows APIs (hid.dll and setupapi.dll via ctypes).
  • Zero external pip dependencies required (runs on stock Python 3.8+ on Windows).
  • Reads CPU temperatures cleanly via Core Temp shared memory (CoreTempMappingObject)
    or HWiNFO shared memory (Global\\HWiNFO_SENS_SM2).
  • Native NVIDIA GPU temperature monitoring via nvidia-smi.
  • Silent background execution mode using pythonw.exe (no persistent command prompt window).
  • Automatic startup installation with Windows Task Scheduler (single command).
"""

import argparse
import ctypes
from ctypes import wintypes
import mmap
import os
import struct
import subprocess
import sys
import textwrap
import time
from typing import Dict, List, Optional, Tuple

# --- Hardware Constants for Ocypus Digital ---
VID = 0x1A2C
PID = 0x434D
TARGET_USAGE_PAGE = 0xFF01
TARGET_USAGE = 0x0001
REPORT_ID = 0x07

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(SCRIPT_DIR, "ocypus_driver.pid")
TASK_NAME = "OcypusDigitalDriver"

# --- Native Windows DLLs ---
hid_dll = ctypes.WinDLL("hid.dll")
setupapi = ctypes.WinDLL("setupapi.dll")
kernel32 = ctypes.WinDLL("kernel32.dll")


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


setupapi.SetupDiGetClassDevsA.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsA.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_char_p,
    ctypes.c_void_p,
    wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(GUID),
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailA.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

kernel32.CreateFileA.restype = ctypes.c_void_p
kernel32.CreateFileA.argtypes = [
    ctypes.c_char_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

hid_dll.HidD_GetPreparsedData.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
hid_dll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid_dll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid_dll.HidD_SetFeature.restype = wintypes.BOOLEAN
hid_dll.HidD_SetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]


class OcypusController:
    """Manages the Ocypus Digital USB LCD device on Windows."""

    def __init__(self):
        self.handle: Optional[int] = None
        self.device_path: Optional[str] = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @staticmethod
    def list_collections() -> List[Dict]:
        """Enumerates all HID collections under VID_1A2C & PID_434D."""
        hid_guid = GUID()
        hid_dll.HidD_GetHidGuid(ctypes.byref(hid_guid))
        h_dev_info = setupapi.SetupDiGetClassDevsA(
            ctypes.byref(hid_guid), None, None, 0x02 | 0x10
        )

        results = []
        idx = 0
        while True:
            iface_data = SP_DEVICE_INTERFACE_DATA()
            iface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                h_dev_info, None, ctypes.byref(hid_guid), idx, ctypes.byref(iface_data)
            ):
                break
            idx += 1

            req_size = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailA(
                h_dev_info, ctypes.byref(iface_data), None, 0, ctypes.byref(req_size), None
            )
            buf = ctypes.create_string_buffer(req_size.value)
            cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 5
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = cb_size

            if setupapi.SetupDiGetDeviceInterfaceDetailA(
                h_dev_info, ctypes.byref(iface_data), buf, req_size, None, None
            ):
                path = ctypes.string_at(ctypes.addressof(buf) + 4)
                if f"vid_{VID:04x}&pid_{PID:04x}".encode() in path.lower():
                    h_file = kernel32.CreateFileA(
                        path, 0x80000000 | 0x40000000, 0x03, None, 3, 0, None
                    )
                    rw_ok = h_file != ctypes.c_void_p(-1).value and bool(h_file)
                    if not rw_ok:
                        h_file = kernel32.CreateFileA(path, 0, 0x03, None, 3, 0, None)

                    preparsed = ctypes.c_void_p()
                    caps = HIDP_CAPS()
                    if hid_dll.HidD_GetPreparsedData(h_file, ctypes.byref(preparsed)):
                        hid_dll.HidP_GetCaps(preparsed, ctypes.byref(caps))
                        hid_dll.HidD_FreePreparsedData(preparsed)
                    kernel32.CloseHandle(h_file)

                    results.append(
                        {
                            "path": path.decode(errors="ignore"),
                            "rw_open": rw_ok,
                            "usage_page": caps.UsagePage,
                            "usage": caps.Usage,
                            "feature_len": caps.FeatureReportByteLength,
                            "output_len": caps.OutputReportByteLength,
                        }
                    )

        setupapi.SetupDiDestroyDeviceInfoList(h_dev_info)
        return results

    def open(self) -> bool:
        """Opens specifically the LCD display collection (UsagePage=0xFF01, Usage=0x0001)."""
        for col in self.list_collections():
            if (
                col["rw_open"]
                and col["usage_page"] == TARGET_USAGE_PAGE
                and col["usage"] == TARGET_USAGE
            ):
                h_file = kernel32.CreateFileA(
                    col["path"].encode(),
                    0x80000000 | 0x40000000,
                    0x03,
                    None,
                    3,
                    0,
                    None,
                )
                if h_file != ctypes.c_void_p(-1).value and h_file:
                    self.handle = h_file
                    self.device_path = col["path"]
                    return True
        return False

    def close(self):
        """Closes the device handle."""
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None
            self.device_path = None

    def send_temperature(
        self, temp_celsius: float, unit: str = "c", led_on: bool = True
    ) -> bool:
        """Sends a 65-byte Feature Report to update temperature digits on the display."""
        if not self.handle:
            return False

        is_fahrenheit = unit.lower() == "f"
        if is_fahrenheit:
            display_temp = int(round(temp_celsius * 9.0 / 5.0 + 32.0))
        else:
            display_temp = int(round(temp_celsius))

        display_temp = max(0, min(199, display_temp))

        buf = (ctypes.c_ubyte * 65)()
        buf[0] = REPORT_ID
        buf[1] = 0xFF
        buf[2] = 0x01 if is_fahrenheit else 0x00

        if led_on:
            buf[3] = display_temp // 100
            buf[4] = (display_temp // 10) % 10
            buf[5] = display_temp % 10
        else:
            buf[3] = 0x00
            buf[4] = 0x00
            buf[5] = 0x00

        ok = hid_dll.HidD_SetFeature(self.handle, ctypes.byref(buf), 65)
        if not ok:
            ok = hid_dll.HidD_SetFeature(self.handle, ctypes.byref(buf), 64)
        return bool(ok)

    def blank_display(self) -> bool:
        """Blanks the LCD screen."""
        return self.send_temperature(0, unit="c", led_on=False)


# --- Core Temp Shared Memory Structures (CoreTempMappingObject) ---

class CoreTempSharedData(ctypes.Structure):
    _fields_ = [
        ("uiLoad", ctypes.c_uint * 256),
        ("uiTjMax", ctypes.c_uint * 128),
        ("uiCoreCnt", ctypes.c_uint),
        ("uiCPUCnt", ctypes.c_uint),
        ("fTemp", ctypes.c_float * 256),
        ("fVID", ctypes.c_float),
        ("fCPUSpeed", ctypes.c_float),
        ("fFSBSpeed", ctypes.c_float),
        ("fMultiplier", ctypes.c_float),
        ("sCPUName", ctypes.c_char * 100),
        ("ucIsFahrenheit", ctypes.c_ubyte),
        ("ucDataDelta", ctypes.c_ubyte),
    ]


def read_coretemp_shared_memory() -> Dict[str, float]:
    """Reads CPU temperature from Core Temp shared memory."""
    sensors: Dict[str, float] = {}
    try:
        shm = mmap.mmap(-1, ctypes.sizeof(CoreTempSharedData), "CoreTempMappingObject", access=mmap.ACCESS_READ)
        data = CoreTempSharedData.from_buffer_copy(shm)
        shm.close()

        core_cnt = min(data.uiCoreCnt, 256)
        is_f = bool(data.ucIsFahrenheit)
        cpu_name = data.sCPUName.decode('utf-8', errors='ignore').strip() or "CPU"

        valid_temps = []
        for i in range(core_cnt):
            t = data.fTemp[i]
            if is_f:
                t = (t - 32.0) * 5.0 / 9.0
            if 0 < t < 125:
                valid_temps.append(t)

        if valid_temps:
            sensors[f"CoreTemp:{cpu_name}"] = max(valid_temps)
    except Exception:
        pass
    return sensors


def read_hwinfo_shared_memory() -> Dict[str, float]:
    """Reads CPU/System temperature from HWiNFO shared memory (Global\\HWiNFO_SENS_SM2)."""
    sensors: Dict[str, float] = {}
    try:
        shm = mmap.mmap(-1, 65536, "Global\\HWiNFO_SENS_SM2", access=mmap.ACCESS_READ)
        if shm[:4] != b"SiWH":
            shm.close()
            return sensors
        (
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            off_reading,
            sz_reading,
            num_reading,
        ) = struct.unpack("<IIIqIIIIII", shm[:44])

        total_size = off_reading + sz_reading * num_reading
        shm.close()
        shm = mmap.mmap(
            -1, total_size, "Global\\HWiNFO_SENS_SM2", access=mmap.ACCESS_READ
        )

        for i in range(num_reading):
            pos = off_reading + i * sz_reading
            entry = shm[pos : pos + sz_reading]
            r_type = struct.unpack("<I", entry[:4])[0]
            if r_type == 1:  # SENSOR_TYPE_TEMP
                label_orig = (
                    entry[16:144].split(b"\x00")[0].decode("mbcs", errors="ignore")
                )
                label_user = (
                    entry[144:272].split(b"\x00")[0].decode("mbcs", errors="ignore")
                )
                value = struct.unpack("<d", entry[288:296])[0]
                sensors[f"HWiNFO:{label_user or label_orig}"] = float(value)
        shm.close()
    except Exception:
        pass
    return sensors


def read_nvidia_gpu_temp() -> Dict[str, float]:
    """Reads NVIDIA GPU temperature using nvidia-smi (native, non-admin)."""
    sensors: Dict[str, float] = {}
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout.strip():
            val = float(res.stdout.strip().splitlines()[0])
            sensors["NVIDIA:GPU Temperature"] = val
    except Exception:
        pass
    return sensors


def get_all_temperatures() -> Dict[str, float]:
    """Collects temperatures from all available reliable sources."""
    sensors: Dict[str, float] = {}
    sensors.update(read_coretemp_shared_memory())
    sensors.update(read_hwinfo_shared_memory())
    sensors.update(read_nvidia_gpu_temp())
    return sensors


def select_sensor(
    sensors: Dict[str, float], keyword: str
) -> Optional[Tuple[str, float]]:
    if not sensors:
        return None

    if keyword and keyword.lower() != "auto":
        for name, temp in sensors.items():
            if keyword.lower() in name.lower() and temp > 0:
                return name, temp

    # Priority order: CPU sensors first, fallback to GPU
    for pref in [
        "coretemp",
        "tctl",
        "tdie",
        "cpu package",
        "cpu",
        "gpu temperature",
        "gpu",
    ]:
        for name, temp in sensors.items():
            if pref in name.lower() and temp > 0:
                return name, temp

    for name, temp in sensors.items():
        if temp > 0:
            return name, temp

    first_key = next(iter(sensors))
    return first_key, sensors[first_key]


def run_loop(controller: OcypusController, sensor_kw: str, unit: str, rate: float):
    # Save current process PID
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    try:
        while True:
            sensors = get_all_temperatures()
            chosen = select_sensor(sensors, sensor_kw)

            if chosen:
                name, temp_c = chosen
                controller.send_temperature(temp_c, unit=unit, led_on=True)
                if sys.stdout and sys.stdout.isatty():
                    disp = temp_c if unit.lower() == "c" else (temp_c * 9.0 / 5.0 + 32.0)
                    sym = "°C" if unit.lower() == "c" else "°F"
                    print(
                        f"\r[OK] Sensor: {name} | Screen: {int(round(disp))}{sym} ({temp_c:.1f}°C)      ",
                        end="",
                        flush=True,
                    )
            time.sleep(rate)
    except KeyboardInterrupt:
        pass
    finally:
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass


def is_process_running(pid: int) -> bool:
    try:
        res = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        )
        return str(pid) in res.stdout
    except Exception:
        return False


def get_running_driver_pid() -> Optional[int]:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            if is_process_running(pid):
                return pid
        except Exception:
            pass
    return None


def start_background(unit: str, sensor: str, rate: float):
    """Starts the driver silently in the background using pythonw.exe."""
    existing_pid = get_running_driver_pid()
    if existing_pid:
        print(f"[!] Driver is already running in background (PID: {existing_pid}).")
        return

    pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw_exe):
        pythonw_exe = sys.executable

    script_path = os.path.abspath(__file__)
    cmd = [pythonw_exe, script_path, "on", "-u", unit, "-s", sensor, "-r", str(rate)]

    proc = subprocess.Popen(
        cmd,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

    time.sleep(1.0)
    print(f"[+] Driver started in the background (PID: {proc.pid})!")
    print("    Your cooler display is now updating.")
    print("    To stop the background process: python ocypus_driver.py stop")


def stop_background():
    """Stops the background driver process and blanks the cooler display."""
    pid = get_running_driver_pid()
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            print(f"[+] Stopped background process (PID: {pid}).")
        except Exception as e:
            print(f"[-] Error terminating process: {e}")
    else:
        script_name = os.path.basename(__file__)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -match '{script_name}' -and $_.ProcessId -ne {os.getpid()} }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
            capture_output=True,
        )
        print("[+] Background driver processes terminated.")

    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

    with OcypusController() as ctrl:
        if ctrl.handle:
            ctrl.blank_display()
            print("[+] Cooler display blanked.")


def status_background():
    pid = get_running_driver_pid()
    if pid:
        print(f"[+] Driver is ACTIVE and running in background (PID: {pid}).")
    else:
        print("[-] Driver is STOPPED (no background process detected).")


def install_startup(unit: str, sensor: str, rate: float):
    """Configures the driver to run silently upon Windows login via Task Scheduler."""
    script_path = os.path.abspath(__file__)
    pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw_exe):
        pythonw_exe = sys.executable

    action_cmd = f'"{pythonw_exe}" "{script_path}" on -u {unit} -s "{sensor}" -r {rate}'

    cmd = [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        action_cmd,
        "/SC",
        "ONLOGON",
        "/F",
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"[+] Automatic startup configured successfully!")
        print(f"    Task '{TASK_NAME}' registered in Windows Task Scheduler.")
        print("    The driver will launch silently every time you log in to Windows.")
        print(f"\n    To start it right now: python ocypus_driver.py start")
        print(f"    To remove from startup: python ocypus_driver.py uninstall-startup")
    except subprocess.CalledProcessError as e:
        print(f"[-] Error registering task: {e.stderr}")


def uninstall_startup():
    """Removes the scheduled startup task."""
    cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"[+] Automatic startup removed (Task '{TASK_NAME}' deleted).")
    except subprocess.CalledProcessError as e:
        print(f"[-] Error removing task: {e.stderr}")


def main():
    parser = argparse.ArgumentParser(
        description="Open-Source Windows Driver for Ocypus Digital Coolers (A40)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Management Commands:
          python ocypus_driver.py start              # Start driver silently in background
          python ocypus_driver.py stop               # Stop background driver and blank screen
          python ocypus_driver.py status             # Check if background driver is active
          python ocypus_driver.py install-startup    # Enable auto-start on Windows login
          python ocypus_driver.py uninstall-startup  # Disable auto-start
          python ocypus_driver.py list               # List active sensors and HID collections
          python ocypus_driver.py on                 # Run in foreground (prints to terminal)
          python ocypus_driver.py off                # Blank the cooler display
        """),
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Start background
    start_parser = subparsers.add_parser("start", help="Start the driver silently in background")
    start_parser.add_argument("-u", "--unit", choices=["c", "f"], default="c")
    start_parser.add_argument("-s", "--sensor", default="auto")
    start_parser.add_argument("-r", "--rate", type=float, default=1.0)

    # Stop background
    subparsers.add_parser("stop", help="Stop background driver and blank display")

    # Status
    subparsers.add_parser("status", help="Check status of background driver")

    # Install / Uninstall Startup
    inst_parser = subparsers.add_parser("install-startup", help="Configure auto-start on Windows login")
    inst_parser.add_argument("-u", "--unit", choices=["c", "f"], default="c")
    inst_parser.add_argument("-s", "--sensor", default="auto")
    inst_parser.add_argument("-r", "--rate", type=float, default=1.0)

    subparsers.add_parser("uninstall-startup", help="Remove from Windows startup")

    # List
    subparsers.add_parser("list", help="List cooler interfaces and active sensors")

    # On (Foreground)
    on_parser = subparsers.add_parser("on", help="Run in foreground (display readings in terminal)")
    on_parser.add_argument("-u", "--unit", choices=["c", "f"], default="c")
    on_parser.add_argument("-s", "--sensor", default="auto")
    on_parser.add_argument("-r", "--rate", type=float, default=1.0)

    # Off
    subparsers.add_parser("off", help="Blank the cooler display")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "start":
        start_background(args.unit, args.sensor, args.rate)

    elif args.command == "stop":
        stop_background()

    elif args.command == "status":
        status_background()

    elif args.command == "install-startup":
        install_startup(args.unit, args.sensor, args.rate)

    elif args.command == "uninstall-startup":
        uninstall_startup()

    elif args.command == "list":
        cols = OcypusController.list_collections()
        print(f"=== Ocypus HID Collections (VID_1A2C & PID_434D): {len(cols)} found ===")
        for c in cols:
            marker = (
                " <-- [COOLER LCD PANEL]"
                if (c["usage_page"] == TARGET_USAGE_PAGE and c["usage"] == TARGET_USAGE)
                else ""
            )
            print(
                f"  UsagePage=0x{c['usage_page']:04X} | Usage=0x{c['usage']:04X} | "
                f"FeatLen={c['feature_len']:2d} | RW={str(c['rw_open']):5s}{marker}"
            )

        print("\n=== Active Temperature Sensors ===")
        sensors = get_all_temperatures()
        if sensors:
            for k, v in sorted(sensors.items()):
                print(f"  {k}: {v:.1f}°C")
        else:
            print("  No active CPU sensor found.")
            print("  -> Ensure Core Temp (with 'Enable shared memory') or HWiNFO is running.")

    elif args.command == "on":
        with OcypusController() as ctrl:
            if not ctrl.handle:
                print("[-] Error: Ocypus cooler display panel not found.")
                return
            run_loop(ctrl, args.sensor, args.unit, args.rate)

    elif args.command == "off":
        with OcypusController() as ctrl:
            if ctrl.handle and ctrl.blank_display():
                print("[+] Display turned off successfully.")
            else:
                print("[-] Failed to turn off display.")


if __name__ == "__main__":
    main()
