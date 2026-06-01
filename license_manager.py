"""
OKX Trading Signal Analysis System -- Client Authorization Management Module
============================================================================
Features:
  1. Show key input dialog on first launch
  2. Validate key -> Bind to machine -> Encrypted storage
  3. Auto-validate on subsequent launches, no re-entry needed
  4. Show reminder 3 days before expiry
  5. Auto-exit on expiry

Binding mechanism: After activation, the key is bound to MAC + disk serial number,
copying to another machine will be invalid
"""

import struct
import hashlib
import zlib
import os
import sys
import json
import uuid
import subprocess
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from Crypto.Cipher import AES

# ============================================================
# AES Key - Consistent with keygen.py
# ============================================================
_AES_KEY = bytes([
    0x7a, 0x3f, 0xc1, 0x8e, 0x2d, 0x54, 0x9b, 0x6f,
    0x11, 0x88, 0x4a, 0xde, 0x33, 0x75, 0xac, 0x19,
    0x5e, 0x42, 0xf7, 0x0b, 0x96, 0xc8, 0x23, 0x6d,
    0x8a, 0x1f, 0xbc, 0x37, 0xe9, 0x50, 0xd4, 0x28,
])

_BASE32_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# License file path
_LICENSE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "okx_trading")
_LICENSE_FILE = os.path.join(_LICENSE_DIR, "license.dat")

PLAN_NAMES = {0: "Trial 1 Day", 1: "Monthly", 2: "Quarterly", 3: "Yearly", 4: "Permanent"}
PLAN_DAYS = {0: 1, 1: 30, 2: 90, 3: 365, 4: 99999}


def _base32_decode(s: str) -> bytes:
    s = s.upper().replace("-", "").replace(" ", "")
    value = 0
    bits = 0
    for c in s:
        if c not in _BASE32_ALPHABET:
            raise ValueError(f"Invalid character: {c}")
        value = (value << 5) | _BASE32_ALPHABET.index(c)
        bits += 5
    byte_count = bits // 8
    value >>= (bits - byte_count * 8)
    return value.to_bytes(byte_count, "big")


def _unpad(data: bytes) -> bytes:
    n = data[-1]
    if n > 16 or n == 0:
        raise ValueError("Invalid padding")
    return data[:-n]


def _get_machine_id() -> str:
    """Get unique machine identifier (MAC + disk serial number hash)"""
    try:
        # MAC address
        node = uuid.getnode()
        mac = f"{node:012x}"

        # Disk serial number (Windows)
        serial = ""
        try:
            result = subprocess.run(
                ["wmic", "diskdrive", "get", "serialnumber"],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                serial = lines[1].strip()
        except Exception:
            serial = os.environ.get("COMPUTERNAME", "unknown")

        combined = f"{mac}|{serial}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:16]


def validate_key(key_str: str) -> dict:
    """
    Validate key format and checksum, return payload info
    Returns: {"valid": bool, "expiry_days": int, "plan_type": int, "expiry_date": str}
    """
    try:
        # Clean format
        key_str = key_str.strip().upper()
        if key_str.startswith("OKX-"):
            key_str = key_str[4:]
        encoded = key_str.replace("-", "").replace(" ", "")

        if len(encoded) < 20:
            return {"valid": False, "reason": "Invalid key format"}

        # Base32 decode
        raw = _base32_decode(encoded)

        # AES decrypt
        cipher = AES.new(_AES_KEY, AES.MODE_ECB)
        decrypted = _unpad(cipher.decrypt(raw))

        if len(decrypted) < 12:
            return {"valid": False, "reason": "Key data incomplete"}

        # Parse payload
        expiry_days, plan_type, salt = struct.unpack(">IB3s", decrypted[:8])
        stored_crc = struct.unpack(">I", decrypted[8:12])[0]
        calc_crc = zlib.crc32(decrypted[:8]) & 0xFFFFFFFF

        if stored_crc != calc_crc:
            return {"valid": False, "reason": "Key checksum failed (may have been tampered with)"}

        if plan_type not in (0, 1, 2, 3, 4):
            return {"valid": False, "reason": "Invalid key"}

        # Calculate expiry date
        base_date = datetime(2025, 1, 1)
        expiry_date = base_date.fromordinal(base_date.toordinal() + expiry_days)

        return {
            "valid": True,
            "expiry_days": expiry_days,
            "plan_type": plan_type,
            "expiry_date": expiry_date.strftime("%Y-%m-%d"),
            "plan_name": PLAN_NAMES[plan_type],
        }

    except Exception as e:
        return {"valid": False, "reason": f"Invalid key ({str(e)[:50]})"}


def save_license(key_str: str, license_info: dict):
    """Encrypt and save authorization info to local file (bound to machine ID)"""
    os.makedirs(_LICENSE_DIR, exist_ok=True)

    machine_id = _get_machine_id()
    data = {
        "key": key_str,
        "machine_id": machine_id,
        "expiry_days": license_info["expiry_days"],
        "plan_type": license_info["plan_type"],
        "activated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    plain = json.dumps(data, ensure_ascii=False).encode("utf-8")

    # Encrypt with machine-derived key
    derived_key = hashlib.sha256(machine_id.encode()).digest()
    cipher = AES.new(derived_key, AES.MODE_ECB)
    pad_len = 16 - len(plain) % 16
    padded = plain + bytes([pad_len]) * pad_len
    encrypted = cipher.encrypt(padded)

    with open(_LICENSE_FILE, "wb") as f:
        f.write(encrypted)


def load_license() -> dict | None:
    """Load and validate authorization from local file"""
    if not os.path.exists(_LICENSE_FILE):
        return None

    try:
        with open(_LICENSE_FILE, "rb") as f:
            encrypted = f.read()

        machine_id = _get_machine_id()
        derived_key = hashlib.sha256(machine_id.encode()).digest()
        cipher = AES.new(derived_key, AES.MODE_ECB)
        decrypted = cipher.decrypt(encrypted)

        pad_len = decrypted[-1]
        if pad_len > 16 or pad_len == 0:
            return None

        plain = decrypted[:-pad_len]
        data = json.loads(plain.decode("utf-8"))

        # Verify machine ID
        if data.get("machine_id") != machine_id:
            return {"valid": False, "reason": "License is bound to another device", "other_machine": True}

        # Verify key is still valid
        key_str = data["key"]
        result = validate_key(key_str)
        if not result["valid"]:
            return {"valid": False, "reason": "License has expired"}

        result["key"] = key_str
        return result

    except Exception:
        return None


def is_expired(license_info: dict) -> bool:
    """Check if expired"""
    if license_info.get("plan_type") == 4:
        return False  # Permanent license never expires
    expiry_date_str = license_info.get("expiry_date", "")
    try:
        expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        return datetime.now() > expiry
    except Exception:
        return True


def days_until_expiry(license_info: dict) -> int:
    """Days until expiry"""
    if license_info.get("plan_type") == 4:
        return 99999
    try:
        expiry = datetime.strptime(license_info["expiry_date"], "%Y-%m-%d")
        return (expiry - datetime.now()).days
    except Exception:
        return -1


def show_activation_dialog() -> str | None:
    """Show key input dialog, return entered key or None"""
    dialog = tk.Tk()
    dialog.title("OKX Trading Signal Analysis System -- License Activation")
    dialog.geometry("500x280")
    dialog.resizable(False, False)
    dialog.configure(bg="#0d1321")

    # Center on screen
    dialog.update_idletasks()
    sw = dialog.winfo_screenwidth()
    sh = dialog.winfo_screenheight()
    x = (sw - 500) // 2
    y = (sh - 280) // 2
    dialog.geometry(f"+{x}+{y}")

    result = {"key": None}

    title = tk.Label(dialog, text="Please enter your license key",
                     font=("Microsoft YaHei", 14, "bold"),
                     fg="#ffffff", bg="#0d1321")
    title.pack(pady=(30, 8))

    hint = tk.Label(dialog, text="Format: OKX-XXXXX-XXXXX-XXXXX-XXXXX-XXXXXX",
                    font=("Microsoft YaHei", 9),
                    fg="#6b7280", bg="#0d1321")
    hint.pack()

    # Input field
    entry_frame = tk.Frame(dialog, bg="#0d1321")
    entry_frame.pack(pady=(20, 10))

    key_var = tk.StringVar()
    key_entry = tk.Entry(entry_frame, textvariable=key_var,
                         font=("Consolas", 12), width=34,
                         bg="#121926", fg="#22c55e",
                         insertbackground="#22c55e",
                         relief="flat", justify="center")
    key_entry.pack(ipady=6)
    key_entry.focus()

    # Error hint
    error_var = tk.StringVar()
    error_label = tk.Label(dialog, textvariable=error_var,
                           font=("Microsoft YaHei", 9),
                           fg="#ef4444", bg="#0d1321")
    error_label.pack()

    def do_activate():
        key = key_var.get().strip()
        if not key:
            error_var.set("Please enter a key")
            return

        info = validate_key(key)
        if not info["valid"]:
            error_var.set(info.get("reason", "Invalid key"))
            return

        result["key"] = key
        result["info"] = info
        dialog.destroy()

    def do_cancel():
        dialog.destroy()

    btn_frame = tk.Frame(dialog, bg="#0d1321")
    btn_frame.pack(pady=(10, 0))

    tk.Button(btn_frame, text="Activate", font=("Microsoft YaHei", 11, "bold"),
              bg="#3b82f6", fg="#ffffff", relief="flat",
              padx=24, pady=6, cursor="hand2", command=do_activate).pack(side=tk.LEFT, padx=6)

    tk.Button(btn_frame, text="Exit", font=("Microsoft YaHei", 11),
              bg="#1e2738", fg="#c9d1d9", relief="flat",
              padx=24, pady=6, cursor="hand2", command=do_cancel).pack(side=tk.LEFT, padx=6)

    dialog.protocol("WM_DELETE_WINDOW", do_cancel)
    dialog.mainloop()
    return result.get("key"), result.get("info")


def check_license_on_startup():
    """
    Called at program startup, returns True if authorization is valid and program can continue.
    If authorization is invalid or expired, shows dialog and sys.exit()
    """
    license_info = load_license()

    # Case 1: No authorization -> show activation dialog
    if license_info is None:
        key, info = show_activation_dialog()
        if key is None or info is None:
            messagebox.showinfo("Not Activated", "No valid key entered, the program will exit.")
            sys.exit(0)

        save_license(key, info)
        days_left = days_until_expiry(info)
        plan = info.get("plan_name", "")
        expiry = info.get("expiry_date", "")
        messagebox.showinfo(
            "Activation Successful",
            f"License Type: {plan}\nExpiry Date: {expiry}\nDays Remaining: {days_left if days_left < 99999 else 'Permanent'} days\n\nBound to this machine, no re-entry needed on restart."
        )
        return True

    # Case 2: Bound to another machine
    if license_info.get("other_machine"):
        messagebox.showerror("License Error", "This license is bound to another device. Please contact the seller to purchase a new key.")
        # Delete invalid license file
        try:
            os.remove(_LICENSE_FILE)
        except Exception:
            pass
        sys.exit(0)

    # Case 3: Invalid authorization
    if not license_info.get("valid", False):
        messagebox.showerror("License Error", license_info.get("reason", "Invalid license."))
        try:
            os.remove(_LICENSE_FILE)
        except Exception:
            pass
        # Re-show activation dialog
        key, info = show_activation_dialog()
        if key is None or info is None:
            sys.exit(0)
        save_license(key, info)
        return True

    # Case 4: Expired
    if is_expired(license_info):
        messagebox.showerror("License Expired", "Your license has expired. Please contact the seller to purchase a renewal key.")
        try:
            os.remove(_LICENSE_FILE)
        except Exception:
            pass
        sys.exit(0)

    # Case 5: Expiring soon (within 3 days)
    days_left = days_until_expiry(license_info)
    if 0 <= days_left <= 3:
        expiry = license_info.get("expiry_date", "")
        messagebox.showwarning(
            "License Expiring Soon",
            f"Your license will expire on {expiry}, with {days_left} days remaining.\nPlease contact the seller to renew."
        )

    return True


# ============================================================
# Standalone entry point: buyers can double-click this file to test activation
# ============================================================
if __name__ == "__main__":
    try:
        check_license_on_startup()
        print("License verification passed. The program can be used normally.")
        input("Press Enter to exit...")
    except SystemExit:
        pass
    except Exception as e:
        messagebox.showerror("Runtime Error", f"Program error: {e}")
        raise
