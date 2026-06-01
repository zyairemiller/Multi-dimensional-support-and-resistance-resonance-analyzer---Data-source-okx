"""
OKX 交易信号分析系统 — 密钥生成器 (仅卖方持有)
=================================================
生成格式: OKX-XXXXX-XXXXX-XXXXX-XXXXX
套餐: 月度 / 季度 / 年度 / 永久

算法: AES-256-ECB 加密载荷 + Base32 编码
载荷: 到期时间戳(4B) | 套餐类型(1B) | 随机盐(3B) | CRC32(4B) = 12B → Base32 → 20字符
"""

import struct
import os
import hashlib
import zlib
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
from Crypto.Cipher import AES

# ============================================================
# AES 密钥 (256-bit) — 与客户端 license_manager.py 中保持一致
# ============================================================
_AES_KEY = bytes([
    0x7a, 0x3f, 0xc1, 0x8e, 0x2d, 0x54, 0x9b, 0x6f,
    0x11, 0x88, 0x4a, 0xde, 0x33, 0x75, 0xac, 0x19,
    0x5e, 0x42, 0xf7, 0x0b, 0x96, 0xc8, 0x23, 0x6d,
    0x8a, 0x1f, 0xbc, 0x37, 0xe9, 0x50, 0xd4, 0x28,
])

_BASE32_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 不含 0/O/1/I 避免混淆

PLAN_NAMES = {0: "试用1天", 1: "月度", 2: "季度", 3: "年度", 4: "永久"}
PLAN_DAYS = {0: 1, 1: 30, 2: 90, 3: 365, 4: 99999}  # 永久用大数字表示


def _base32_encode(data: bytes) -> str:
    """将字节数据编码为 Base32 字符串 (Crockford 风格, 5 bits/char)"""
    bits = 0
    value = 0
    result = []
    for byte in data:
        value = (value << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            result.append(_BASE32_ALPHABET[(value >> bits) & 0x1F])
    if bits > 0:
        result.append(_BASE32_ALPHABET[(value << (5 - bits)) & 0x1F])
    return "".join(result)


def _base32_decode(s: str) -> bytes:
    """将 Base32 字符串解码为字节"""
    s = s.upper().replace("-", "").replace(" ", "")
    value = 0
    bits = 0
    for c in s:
        if c not in _BASE32_ALPHABET:
            raise ValueError(f"无效字符: {c}")
        value = (value << 5) | _BASE32_ALPHABET.index(c)
        bits += 5
    # 去掉多余的 padding bits
    byte_count = (bits // 8)
    value >>= (bits - byte_count * 8)
    return value.to_bytes(byte_count, "big")


def _pad(data: bytes, block_size: int = 16) -> bytes:
    """PKCS7 填充"""
    n = block_size - len(data) % block_size
    return data + bytes([n]) * n


def _unpad(data: bytes) -> bytes:
    """PKCS7 去填充"""
    n = data[-1]
    if n > 16 or n == 0:
        raise ValueError("无效的填充")
    return data[:-n]


def generate_key(plan_type: int) -> str:
    """
    生成一个授权密钥

    载荷 (12 字节):
      Bytes 0-3: 到期时间戳 (uint32, big-endian, days since 2025-01-01)
      Byte  4:   套餐类型 (0=月度, 1=季度, 2=年度, 3=永久)
      Bytes 5-7: 随机盐 (3 字节)
      Bytes 8-11: CRC32 校验 (4 字节)

    AES-256-ECB 加密 → Base32 编码 → OKX-XXXXX-XXXXX-XXXXX-XXXXX
    """
    now = datetime.now()
    days = PLAN_DAYS[plan_type]
    expiry = now + timedelta(days=days)
    expiry_days = (expiry - datetime(2025, 1, 1)).days

    # 构建载荷
    salt = os.urandom(3)
    payload = struct.pack(">IB3s", expiry_days, plan_type, salt)
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    payload += struct.pack(">I", checksum)
    # payload = 12 bytes

    # AES 加密
    cipher = AES.new(_AES_KEY, AES.MODE_ECB)
    encrypted = cipher.encrypt(_pad(payload, 16))

    # Base32 编码 (16 bytes → 26 chars)，格式化为 5+5+5+5+6
    encoded = _base32_encode(encrypted)
    return f"OKX-{encoded[:5]}-{encoded[5:10]}-{encoded[10:15]}-{encoded[15:20]}-{encoded[20:26]}"


# ============================================================
# GUI
# ============================================================
class KeyGenApp:
    def __init__(self, root):
        self.root = root
        root.title("OKX 密钥分发器")
        root.geometry("460x340")
        root.resizable(False, False)
        root.configure(bg="#0d1321")

        style = ttk.Style()
        style.theme_use("clam")

        # 标题
        title = tk.Label(root, text="OKX 交易信号分析系统 — 密钥分发器",
                         font=("Microsoft YaHei", 13, "bold"),
                         fg="#ffffff", bg="#0d1321")
        title.pack(pady=(24, 8))

        subtitle = tk.Label(root, text="仅供卖方使用，请勿分发",
                            font=("Microsoft YaHei", 9),
                            fg="#6b7280", bg="#0d1321")
        subtitle.pack()

        # 套餐选择
        plan_frame = tk.Frame(root, bg="#0d1321")
        plan_frame.pack(pady=(20, 4))
        tk.Label(plan_frame, text="套餐类型:", font=("Microsoft YaHei", 11),
                 fg="#c9d1d9", bg="#0d1321", width=10, anchor="e").pack(side=tk.LEFT, padx=(0, 8))

        self.plan_var = tk.StringVar(value="月度")
        plan_combo = ttk.Combobox(plan_frame, textvariable=self.plan_var,
                                  values=["试用1天", "月度", "季度", "年度", "永久"],
                                  state="readonly", width=12, font=("Microsoft YaHei", 11))
        plan_combo.pack(side=tk.LEFT)

        # 生成按钮
        gen_btn = tk.Button(root, text="生成密钥", font=("Microsoft YaHei", 12, "bold"),
                            bg="#3b82f6", fg="#ffffff", activebackground="#2563eb",
                            activeforeground="#ffffff", relief="flat",
                            padx=30, pady=8, cursor="hand2", command=self.do_generate)
        gen_btn.pack(pady=(24, 16))

        # 结果显示
        result_frame = tk.Frame(root, bg="#121926", highlightbackground="#1e2738",
                                highlightthickness=1)
        result_frame.pack(fill="x", padx=40, pady=(0, 4))
        tk.Label(result_frame, text="生成的密钥:", font=("Microsoft YaHei", 9),
                 fg="#8b949e", bg="#121926").pack(anchor="w", padx=12, pady=(8, 0))

        self.result_var = tk.StringVar()
        self.result_label = tk.Label(result_frame, textvariable=self.result_var,
                                     font=("Consolas", 14, "bold"),
                                     fg="#22c55e", bg="#121926", wraplength=360)
        self.result_label.pack(pady=(2, 12))

        # 复制按钮
        self.copy_btn = tk.Button(root, text="复制密钥", font=("Microsoft YaHei", 10),
                                  bg="#1e2738", fg="#c9d1d9", relief="flat",
                                  padx=16, pady=4, cursor="hand2", command=self.do_copy)
        self.copy_btn.pack()
        self.copy_btn.pack_forget()

        # 到期日期提示
        self.expiry_label = tk.Label(root, text="", font=("Microsoft YaHei", 9),
                                     fg="#f59e0b", bg="#0d1321")
        self.expiry_label.pack(pady=(8, 0))

        self._last_key = ""
        self._last_expiry = ""

    def do_generate(self):
        plan_map = {"试用1天": 0, "月度": 1, "季度": 2, "年度": 3, "永久": 4}
        plan_type = plan_map[self.plan_var.get()]

        key = generate_key(plan_type)

        # 计算到期日
        days = PLAN_DAYS[plan_type]
        now = datetime.now()
        if plan_type == 3:
            expiry_str = "永久有效"
        else:
            expiry = now + timedelta(days=days)
            expiry_str = f"到期日期: {expiry.strftime('%Y-%m-%d')}"

        self._last_key = key
        self._last_expiry = expiry_str
        self.result_var.set(key)
        self.expiry_label.config(text=expiry_str)

        self.copy_btn.pack()

    def do_copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._last_key)
        messagebox.showinfo("已复制", "密钥已复制到剪贴板")


if __name__ == "__main__":
    root = tk.Tk()
    app = KeyGenApp(root)
    root.mainloop()