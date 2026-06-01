"""
OKX 交易信号分析系统 — 客户端授权管理模块
============================================
功能:
  1. 首次启动弹出密钥输入框
  2. 验证密钥 → 绑定本机 → 加密存储
  3. 后续启动自动验证，无需重复输入
  4. 到期前 3 天弹提醒
  5. 到期自动退出

绑定机制: 密钥激活后与 MAC + 硬盘序列号绑定，复制到其他电脑无效
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
# AES 密钥 — 与 keygen.py 保持一致
# ============================================================
_AES_KEY = bytes([
    0x7a, 0x3f, 0xc1, 0x8e, 0x2d, 0x54, 0x9b, 0x6f,
    0x11, 0x88, 0x4a, 0xde, 0x33, 0x75, 0xac, 0x19,
    0x5e, 0x42, 0xf7, 0x0b, 0x96, 0xc8, 0x23, 0x6d,
    0x8a, 0x1f, 0xbc, 0x37, 0xe9, 0x50, 0xd4, 0x28,
])

_BASE32_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# 授权文件路径
_LICENSE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "okx_trading")
_LICENSE_FILE = os.path.join(_LICENSE_DIR, "license.dat")

PLAN_NAMES = {0: "试用1天", 1: "月度", 2: "季度", 3: "年度", 4: "永久"}
PLAN_DAYS = {0: 1, 1: 30, 2: 90, 3: 365, 4: 99999}


def _base32_decode(s: str) -> bytes:
    s = s.upper().replace("-", "").replace(" ", "")
    value = 0
    bits = 0
    for c in s:
        if c not in _BASE32_ALPHABET:
            raise ValueError(f"无效字符: {c}")
        value = (value << 5) | _BASE32_ALPHABET.index(c)
        bits += 5
    byte_count = bits // 8
    value >>= (bits - byte_count * 8)
    return value.to_bytes(byte_count, "big")


def _unpad(data: bytes) -> bytes:
    n = data[-1]
    if n > 16 or n == 0:
        raise ValueError("无效填充")
    return data[:-n]


def _get_machine_id() -> str:
    """获取机器唯一标识符 (MAC + 硬盘序列号 哈希)"""
    try:
        # MAC 地址
        node = uuid.getnode()
        mac = f"{node:012x}"

        # 硬盘序列号 (Windows)
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
    验证密钥格式和校验和，返回载荷信息
    返回: {"valid": bool, "expiry_days": int, "plan_type": int, "expiry_date": str}
    """
    try:
        # 清理格式
        key_str = key_str.strip().upper()
        if key_str.startswith("OKX-"):
            key_str = key_str[4:]
        encoded = key_str.replace("-", "").replace(" ", "")

        if len(encoded) < 20:
            return {"valid": False, "reason": "密钥格式不正确"}

        # Base32 解码
        raw = _base32_decode(encoded)

        # AES 解密
        cipher = AES.new(_AES_KEY, AES.MODE_ECB)
        decrypted = _unpad(cipher.decrypt(raw))

        if len(decrypted) < 12:
            return {"valid": False, "reason": "密钥数据不完整"}

        # 解析载荷
        expiry_days, plan_type, salt = struct.unpack(">IB3s", decrypted[:8])
        stored_crc = struct.unpack(">I", decrypted[8:12])[0]
        calc_crc = zlib.crc32(decrypted[:8]) & 0xFFFFFFFF

        if stored_crc != calc_crc:
            return {"valid": False, "reason": "密钥校验失败（可能被篡改）"}

        if plan_type not in (0, 1, 2, 3, 4):
            return {"valid": False, "reason": "密钥无效"}

        # 计算到期日期
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
        return {"valid": False, "reason": f"密钥无效 ({str(e)[:50]})"}


def save_license(key_str: str, license_info: dict):
    """加密保存授权信息到本地文件 (绑定机器码)"""
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

    # 用机器码派生的密钥加密
    derived_key = hashlib.sha256(machine_id.encode()).digest()
    cipher = AES.new(derived_key, AES.MODE_ECB)
    pad_len = 16 - len(plain) % 16
    padded = plain + bytes([pad_len]) * pad_len
    encrypted = cipher.encrypt(padded)

    with open(_LICENSE_FILE, "wb") as f:
        f.write(encrypted)


def load_license() -> dict | None:
    """从本地文件加载并验证授权"""
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

        # 验证机器码
        if data.get("machine_id") != machine_id:
            return {"valid": False, "reason": "授权已绑定到其他设备", "other_machine": True}

        # 验证密钥是否仍然有效
        key_str = data["key"]
        result = validate_key(key_str)
        if not result["valid"]:
            return {"valid": False, "reason": "授权已失效"}

        result["key"] = key_str
        return result

    except Exception:
        return None


def is_expired(license_info: dict) -> bool:
    """检查是否已过期"""
    if license_info.get("plan_type") == 4:
        return False  # 永久授权不过期
    expiry_date_str = license_info.get("expiry_date", "")
    try:
        expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        return datetime.now() > expiry
    except Exception:
        return True


def days_until_expiry(license_info: dict) -> int:
    """距离到期还有多少天"""
    if license_info.get("plan_type") == 4:
        return 99999
    try:
        expiry = datetime.strptime(license_info["expiry_date"], "%Y-%m-%d")
        return (expiry - datetime.now()).days
    except Exception:
        return -1


def show_activation_dialog() -> str | None:
    """弹出密钥输入对话框，返回输入的密钥或 None"""
    dialog = tk.Tk()
    dialog.title("OKX 交易信号分析系统 — 授权激活")
    dialog.geometry("500x280")
    dialog.resizable(False, False)
    dialog.configure(bg="#0d1321")

    # 居中
    dialog.update_idletasks()
    sw = dialog.winfo_screenwidth()
    sh = dialog.winfo_screenheight()
    x = (sw - 500) // 2
    y = (sh - 280) // 2
    dialog.geometry(f"+{x}+{y}")

    result = {"key": None}

    title = tk.Label(dialog, text="请输入授权密钥",
                     font=("Microsoft YaHei", 14, "bold"),
                     fg="#ffffff", bg="#0d1321")
    title.pack(pady=(30, 8))

    hint = tk.Label(dialog, text="格式: OKX-XXXXX-XXXXX-XXXXX-XXXXX-XXXXXX",
                    font=("Microsoft YaHei", 9),
                    fg="#6b7280", bg="#0d1321")
    hint.pack()

    # 输入框
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

    # 错误提示
    error_var = tk.StringVar()
    error_label = tk.Label(dialog, textvariable=error_var,
                           font=("Microsoft YaHei", 9),
                           fg="#ef4444", bg="#0d1321")
    error_label.pack()

    def do_activate():
        key = key_var.get().strip()
        if not key:
            error_var.set("请输入密钥")
            return

        info = validate_key(key)
        if not info["valid"]:
            error_var.set(info.get("reason", "密钥无效"))
            return

        result["key"] = key
        result["info"] = info
        dialog.destroy()

    def do_cancel():
        dialog.destroy()

    btn_frame = tk.Frame(dialog, bg="#0d1321")
    btn_frame.pack(pady=(10, 0))

    tk.Button(btn_frame, text="激活", font=("Microsoft YaHei", 11, "bold"),
              bg="#3b82f6", fg="#ffffff", relief="flat",
              padx=24, pady=6, cursor="hand2", command=do_activate).pack(side=tk.LEFT, padx=6)

    tk.Button(btn_frame, text="退出", font=("Microsoft YaHei", 11),
              bg="#1e2738", fg="#c9d1d9", relief="flat",
              padx=24, pady=6, cursor="hand2", command=do_cancel).pack(side=tk.LEFT, padx=6)

    dialog.protocol("WM_DELETE_WINDOW", do_cancel)
    dialog.mainloop()
    return result.get("key"), result.get("info")


def check_license_on_startup():
    """
    程序启动时调用，返回 True 表示授权有效可继续运行。
    如果授权无效或过期，弹窗后 sys.exit()
    """
    license_info = load_license()

    # 情况1: 无授权 → 弹出激活框
    if license_info is None:
        key, info = show_activation_dialog()
        if key is None or info is None:
            messagebox.showinfo("未激活", "未输入有效密钥，程序将退出。")
            sys.exit(0)

        save_license(key, info)
        days_left = days_until_expiry(info)
        plan = info.get("plan_name", "")
        expiry = info.get("expiry_date", "")
        messagebox.showinfo(
            "激活成功",
            f"授权类型: {plan}\n到期日期: {expiry}\n剩余天数: {days_left if days_left < 99999 else '永久'} 天\n\n已绑定本机，重启无需再次输入。"
        )
        return True

    # 情况2: 绑定到其他机器
    if license_info.get("other_machine"):
        messagebox.showerror("授权错误", "该授权已绑定到其他设备，请联系购买新密钥。")
        # 删除无效授权文件
        try:
            os.remove(_LICENSE_FILE)
        except Exception:
            pass
        sys.exit(0)

    # 情况3: 授权无效
    if not license_info.get("valid", False):
        messagebox.showerror("授权错误", license_info.get("reason", "授权无效。"))
        try:
            os.remove(_LICENSE_FILE)
        except Exception:
            pass
        # 重新弹出激活框
        key, info = show_activation_dialog()
        if key is None or info is None:
            sys.exit(0)
        save_license(key, info)
        return True

    # 情况4: 已过期
    if is_expired(license_info):
        messagebox.showerror("授权已到期", "您的授权已到期，请联系购买续费密钥。")
        try:
            os.remove(_LICENSE_FILE)
        except Exception:
            pass
        sys.exit(0)

    # 情况5: 即将到期（3天内）
    days_left = days_until_expiry(license_info)
    if 0 <= days_left <= 3:
        expiry = license_info.get("expiry_date", "")
        messagebox.showwarning(
            "授权即将到期",
            f"您的授权将于 {expiry} 到期，剩余 {days_left} 天。\n请及时联系续费。"
        )

    return True


# ============================================================
# 独立运行入口：买家可直接双击此文件测试激活
# ============================================================
if __name__ == "__main__":
    try:
        check_license_on_startup()
        print("授权验证通过，程序可以正常使用。")
        input("按回车键退出...")
    except SystemExit:
        pass
    except Exception as e:
        messagebox.showerror("运行错误", f"程序异常: {e}")
        raise