#!/usr/bin/env python3

import sys
import os
import json
import subprocess
import hashlib
import time
import signal
import struct
import zlib
import lzma
import shutil
import threading
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPushButton, QLabel, QProgressBar,
    QTextEdit, QFileDialog, QComboBox, QCheckBox, QInputDialog,
    QGroupBox, QGridLayout, QMessageBox, QFrame, QHeaderView,
    QDialog, QDialogButtonBox, QLineEdit, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QFontMetrics

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

MAGIC = b"DGB3"
HASH_SIZE = 32
CHUNK_SIZE = 4 * 1024 * 1024
PBKDF2_ITERS = 600000
EXT_FULL = ".full"
EXT_PART = ".part"
DGB_EXT = ".dgb"  # legacy, still accepted for reading

COMP_OPTIONS = {
    "None (Raw)": "none",
    "Zlib (Fast)": "zlib",
    "LZMA (Compact)": "lzma",
}

# Segment types for smart disk backups (chunked multi-partition format)
SEG_PTABLE = b"PTBL"   # Partition table (sfdisk dump)
SEG_BOOT   = b"BOOT"   # Boot sectors (first 1 MB)
SEG_PART   = b"PART"   # Partition image
SEG_END    = b"ENDD"   # End marker

METHOD_PARTCLONE = 0
METHOD_DD = 1

PARTCLONE_FS = {
    "vfat": "vfat", "fat16": "vfat", "fat32": "vfat",
    "ext2": "ext2", "ext3": "ext3", "ext4": "ext4",
    "ntfs": "ntfs", "exfat": "exfat",
    "btrfs": "btrfs", "xfs": "xfs",
    "hfsplus": "hfsplus", "f2fs": "f2fs",
}

STYLESHEET = """
* { font-family: 'Noto Sans', 'Segoe UI', sans-serif; font-size: 13px; }
QMainWindow, QWidget { background-color: #0f0f1a; color: #c8d6e5; }
QDialog { background-color: #0f0f1a; color: #c8d6e5; border-radius: 12px; }

QGroupBox {
    background-color: #16213e; border: 1px solid #1a1a3e; border-radius: 10px;
    margin-top: 14px; padding: 16px 12px 12px 12px; font-weight: bold; color: #00d4aa;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    padding: 4px 14px; background-color: #16213e; border-radius: 6px; color: #00d4aa;
}

QTreeWidget {
    background-color: #0d1b2a; border: 1px solid #1a1a3e; border-radius: 8px;
    padding: 4px; outline: none; color: #c8d6e5; font-size: 12px;
}
QTreeWidget::item { padding: 5px 4px; border-bottom: 1px solid #1a1a3e; }
QTreeWidget::item:selected { background-color: #2a2a5e; color: #00d4aa; }
QTreeWidget::item:hover { background-color: #ffffff08; }
QHeaderView::section {
    background-color: #16213e; color: #00d4aa; padding: 6px 8px;
    border: none; border-bottom: 2px solid #00d4aa44; font-weight: bold; font-size: 12px;
}

QPushButton {
    background-color: #1a1a3e; color: #c8d6e5; border: 1px solid #2a2a5e;
    border-radius: 8px; padding: 8px 20px; font-weight: bold; min-height: 20px;
}
QPushButton:hover { background-color: #2a2a5e; border-color: #00d4aa; color: #00d4aa; }
QPushButton:pressed { background-color: #00d4aa33; }
QPushButton:disabled { background-color: #111; color: #444; border-color: #1a1a2e; }

QPushButton#actionBtn {
    background-color: #16213e; color: #00d4aa; border: 2px solid #00d4aa44;
    border-radius: 10px; padding: 10px 22px; font-size: 13px; font-weight: bold;
    min-width: 100px;
}
QPushButton#actionBtn:hover { background-color: #00d4aa22; border-color: #00d4aa; }
QPushButton#actionBtn:pressed { background-color: #00d4aa33; }
QPushButton#actionBtn:disabled { color: #333; border-color: #1a1a2e; }

QPushButton#startBtn {
    background-color: #00d4aa; color: #0f0f1a; border: none;
    font-size: 14px; padding: 10px 32px; border-radius: 10px;
}
QPushButton#startBtn:hover { background-color: #00e6b8; }
QPushButton#startBtn:disabled { background-color: #1a3a3a; color: #556; }

QPushButton#cancelBtn {
    background-color: #e74c3c; color: #fff; border: none;
    border-radius: 10px; padding: 10px 24px;
}
QPushButton#cancelBtn:hover { background-color: #ff5e4f; }

QPushButton#logToggle {
    background-color: transparent; color: #576574; border: none;
    padding: 4px 12px; font-size: 11px;
}
QPushButton#logToggle:hover { color: #00d4aa; }

QComboBox {
    background-color: #1a1a3e; color: #c8d6e5; border: 1px solid #2a2a5e;
    border-radius: 8px; padding: 6px 12px; min-height: 24px;
}
QComboBox:hover { border-color: #00d4aa; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox QAbstractItemView {
    background-color: #16213e; color: #c8d6e5; border: 1px solid #2a2a5e;
    selection-background-color: #2a2a5e; selection-color: #00d4aa;
}

QCheckBox { color: #c8d6e5; spacing: 8px; padding: 4px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid #555; border-radius: 4px; }
QCheckBox::indicator:checked { background-color: #00d4aa; border-color: #00d4aa; }

QProgressBar {
    background-color: #0d1b2a; border: 1px solid #1a1a3e; border-radius: 8px;
    text-align: center; color: #00d4aa; font-weight: bold; min-height: 22px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00d4aa, stop:1 #00b894);
    border-radius: 7px;
}

QTextEdit {
    background-color: #0a0a18; color: #7f8fa6; border: 1px solid #1a1a3e;
    border-radius: 8px; padding: 8px; font-family: 'Fira Code', monospace; font-size: 11px;
}

QLineEdit {
    background-color: #1a1a3e; color: #c8d6e5; border: 1px solid #2a2a5e;
    border-radius: 8px; padding: 8px 12px; font-size: 13px;
}
QLineEdit:focus { border-color: #00d4aa; }

QLabel { color: #c8d6e5; background: transparent; }
QLabel#titleLabel { font-size: 20px; font-weight: bold; color: #00d4aa; }
QLabel#subtitleLabel { font-size: 11px; color: #576574; }
QLabel#infoKey { color: #576574; font-size: 12px; }
QLabel#infoValue { color: #c8d6e5; font-weight: bold; font-size: 12px; }
QLabel#statusLabel { color: #00d4aa; font-size: 12px; font-weight: bold; }
QLabel#warnLabel { color: #e74c3c; font-size: 12px; font-weight: bold; }
QLabel#metaLabel { color: #8899aa; font-size: 12px; padding: 8px; background-color: #0d1b2a; border-radius: 6px; }

QFrame#separator { background-color: #1a1a3e; max-height: 1px; }

QScrollBar:vertical { background: #0f0f1a; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #2a2a5e; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

def human_size(b):
    if not b: return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i, s = 0, float(b)
    while s >= 1024 and i < len(units) - 1: s /= 1024; i += 1
    return f"{s:.1f} {units[i]}"


class DualProgressBar(QWidget):
    """Progress bar showing two values: usage (background) + progress (foreground)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._usage = 0        # disk usage %
        self._progress = 0     # backup progress %
        self._usage_text = ""
        self._progress_text = ""
        self._show_progress = False
        self.setFixedHeight(28)
        self.setMinimumWidth(200)

    def set_usage(self, pct, text=""):
        self._usage = max(0, min(100, pct))
        self._usage_text = text
        self.update()

    def set_progress(self, pct, text=""):
        self._progress = max(0, min(100, pct))
        self._progress_text = text
        self._show_progress = True
        self.update()

    def clear_progress(self):
        self._progress = 0
        self._progress_text = ""
        self._show_progress = False
        self.update()

    def reset_all(self):
        self._usage = 0
        self._progress = 0
        self._usage_text = ""
        self._progress_text = ""
        self._show_progress = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = 6  # border radius

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#1a1a3e")))
        p.drawRoundedRect(0, 0, w, h, r, r)

        if self._usage > 0:
            uw = max(int(w * self._usage / 100), r * 2)
            p.setBrush(QBrush(QColor(40, 60, 120)))
            p.drawRoundedRect(0, 0, uw, h, r, r)

        if self._show_progress and self._progress > 0:
            pw = max(int(w * self._progress / 100), r * 2)
            p.setBrush(QBrush(QColor(0, 212, 170, 200)))
            p.drawRoundedRect(0, 0, pw, h, r, r)

        font = QFont("Noto Sans", 9, QFont.Weight.Bold)
        p.setFont(font)
        p.setPen(QPen(QColor("#e0e8f0")))
        if self._show_progress and self._progress_text:
            p.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, self._progress_text)
        elif self._usage_text:
            p.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, self._usage_text)

        p.end()

def get_all_usage(block_devs=None):
    """Get usage for ALL block devices (mounted AND unmounted).
    Uses multiple methods for maximum coverage:
    1) lsblk FSUSED  2) df  3) tune2fs (ext)  4) ntfsinfo (ntfs)
    Returns dict: { '/dev/sda1': (pct, used_bytes, total_bytes), ... }"""
    import re
    usage = {}

    # Method 1: lsblk FSUSED (works on unmounted filesystems)
    try:
        r = subprocess.run(
            ["lsblk", "-Pbno", "NAME,FSUSED,FSSIZE"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                vals = dict(re.findall(r'(\w+)="([^"]*)"', line))
                name = vals.get("NAME", "")
                fs_used = vals.get("FSUSED", "").strip()
                fs_size = vals.get("FSSIZE", "").strip()
                if name and fs_used and fs_size:
                    try:
                        u, t = int(fs_used), int(fs_size)
                        if t > 0:
                            usage[name] = (round((u / t) * 100, 1), u, t)
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

    # Method 2: df for mounted filesystems
    try:
        r = subprocess.run(
            ["df", "-B1", "--output=source,used,size,pcent"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        dev = parts[0]
                        if dev not in usage:
                            u = int(parts[1])
                            t = int(parts[2])
                            pct = float(parts[3].rstrip('%'))
                            if t > 0:
                                usage[dev] = (round(pct, 1), u, t)
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

    # Method 3: temporary read-only mounts for unmounted partitions
    if block_devs:
        import tempfile
        for d in block_devs:
            for ch in (d.get("children") or []):
                name = ch.get("name", "")
                fstype = ch.get("fstype") or ""
                if not name or not fstype or name in usage:
                    continue
                if ch.get("mountpoint") or fstype.lower() in ("swap", "linux_raid_member", "LVM2_member"):
                    continue
                tmpdir = None
                try:
                    tmpdir = tempfile.mkdtemp(prefix="dgusage_")
                    mr = subprocess.run(
                        ["mount", "-o", "ro,noexec,nosuid,nodev", name, tmpdir],
                        capture_output=True, timeout=5)
                    if mr.returncode == 0:
                        st = shutil.disk_usage(tmpdir)
                        if st.total > 0:
                            usage[name] = (
                                round((st.used / st.total) * 100, 1),
                                st.used, st.total)
                        subprocess.run(["umount", tmpdir],
                                       capture_output=True, timeout=5)
                except Exception:
                    pass
                finally:
                    if tmpdir:
                        subprocess.run(["umount", tmpdir],
                                       capture_output=True, timeout=3)
                        try:
                            os.rmdir(tmpdir)
                        except Exception:
                            pass

    return usage

def get_disk_usage_from_map(dev_info, usage_map):
    """Look up usage for a device from the pre-built usage_map.
    Returns (used_pct, used_bytes, total_bytes) or (None, 0, 0)."""
    if not isinstance(dev_info, dict):
        return (None, 0, 0)
    name = dev_info.get("name", "")
    if name and name in usage_map:
        return usage_map[name]
    return (None, 0, 0)

def get_block_devices():
    try:
        r = subprocess.run(["lsblk", "-Jbpo",
            "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,LABEL,UUID,RO,TRAN,ROTA,VENDOR,REV,HCTL"],
            capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout).get("blockdevices", []) if r.returncode == 0 else []
    except: return []

def disk_type_label(dev):
    """Return SSD/HDD/USB/NVMe label for a disk device."""
    tran = (dev.get("tran") or "").lower()
    rota = dev.get("rota")
    name = dev.get("name", "")
    if "nvme" in name: return "NVMe"
    if tran == "usb": return "USB"
    if rota is False or rota == "0" or rota == 0: return "SSD"
    if rota is True or rota == "1" or rota == 1: return "HDD"
    return tran.upper() if tran else "-"

def display_fstype(dev):
    """Return human-friendly filesystem name (FAT32 instead of vfat, etc.)."""
    fs = (dev.get("fstype") or "").lower()
    if not fs:
        return "-"
    if fs == "vfat":
        name = dev.get("name", "")
        if name:
            try:
                r = subprocess.run(
                    ["blkid", "-o", "value", "-s", "VERSION", name],
                    capture_output=True, text=True, timeout=3)
                ver = r.stdout.strip().upper()
                if ver in ("FAT12", "FAT16", "FAT32"):
                    return ver
            except Exception:
                pass
        return "FAT32"
    name_map = {"ntfs3": "NTFS", "ntfs": "NTFS", "exfat": "exFAT",
                "btrfs": "Btrfs", "xfs": "XFS", "iso9660": "ISO9660",
                "hfsplus": "HFS+", "f2fs": "F2FS"}
    return name_map.get(fs, fs)

def read_dgb_meta(filepath):
    try:
        with open(filepath, "rb") as f:
            if f.read(4) != MAGIC: return None
            ml = struct.unpack(">I", f.read(4))[0]
            if ml > 1024 * 1024: return None
            return json.loads(f.read(ml))
    except: return None

def derive_key(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITERS, dklen=32)


class BackupDialog(QDialog):
    """Dialog for backup settings."""

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = device
        self.result_data = None
        self.setWindowTitle("Create Backup")
        self.setMinimumWidth(520)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        d = self.device
        bt = "Full Disk" if d.get("type") == "disk" else "Partition"
        info = QLabel(
            f"<b>{d.get('name','')}</b> | {bt} | {human_size(d.get('size'))} | "
            f"{d.get('fstype') or '-'} | {(d.get('model') or '').strip() or '-'}")
        info.setObjectName("metaLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        cr = QHBoxLayout()
        cr.addWidget(QLabel("Compression:"))
        self.comp = QComboBox()
        self.comp.addItems(list(COMP_OPTIONS.keys()))
        self.comp.setCurrentIndex(0)
        self.comp.setFixedWidth(180)
        cr.addWidget(self.comp)
        cr.addStretch()
        layout.addLayout(cr)

        self.encrypt = QCheckBox("Encrypt (AES-256)")
        self.encrypt.setEnabled(HAS_CRYPTO)
        if not HAS_CRYPTO:
            self.encrypt.setToolTip("Install python3-cryptography to enable")
        layout.addWidget(self.encrypt)

        dr = QHBoxLayout()
        dr.addWidget(QLabel("Save to:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Choose destination...")
        self.path_edit.setReadOnly(True)
        dr.addWidget(self.path_edit, 1)
        browse = QPushButton("...")
        browse.setFixedWidth(44)
        browse.clicked.connect(self._browse)
        dr.addWidget(browse)
        layout.addLayout(dr)

        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        self.start = QPushButton("Start Backup")
        self.start.setObjectName("startBtn")
        self.start.setEnabled(False)
        self.start.clicked.connect(self._accept)
        br.addWidget(self.start)
        layout.addLayout(br)

    def _browse(self):
        d = self.device
        is_disk = d.get("type") == "disk"
        prefix = "full" if is_disk else "part"
        ext = EXT_FULL if is_disk else EXT_PART
        name = os.path.basename(d.get("name", "backup"))
        sz = human_size(d.get("size")).replace(" ", "").replace(".", "_")
        default = f"{prefix}_{name}_{sz}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        path, _ = QFileDialog.getSaveFileName(self, "Save Backup", default,
            f"Backup Full (*{EXT_FULL});;Backup Part (*{EXT_PART});;All (*)")
        if path:
            self.path_edit.setText(path)
            self.start.setEnabled(True)

    def _accept(self):
        password = None
        if self.encrypt.isChecked():
            pw, ok = QInputDialog.getText(self, "Password",
                "Enter encryption password:", QLineEdit.EchoMode.Password)
            if not ok or not pw: return
            pw2, ok2 = QInputDialog.getText(self, "Confirm Password",
                "Confirm password:", QLineEdit.EchoMode.Password)
            if not ok2 or pw2 != pw:
                QMessageBox.warning(self, "Error", "Passwords do not match!")
                return
            password = pw

        comp_key = self.comp.currentText()
        self.result_data = {
            "path": self.path_edit.text(),
            "compression": COMP_OPTIONS[comp_key],
            "compression_label": comp_key,
            "password": password,
        }
        self.accept()


class RestoreDialog(QDialog):
    """Dialog for restore settings."""

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = device
        self.result_data = None
        self.meta = None
        self.setWindowTitle("Restore Backup")
        self.setMinimumWidth(540)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        d = self.device
        bt = "Full Disk" if d.get("type") == "disk" else "Partition"
        info = QLabel(
            f"Target: <b>{d.get('name','')}</b> | {bt} | "
            f"{human_size(d.get('size'))} | {d.get('fstype') or '-'}")
        info.setObjectName("metaLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        fr = QHBoxLayout()
        fr.addWidget(QLabel("Backup file:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select .dgb file...")
        self.path_edit.setReadOnly(True)
        fr.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse")
        browse.setFixedWidth(80)
        browse.clicked.connect(self._browse)
        fr.addWidget(browse)
        layout.addLayout(fr)

        self.meta_lbl = QLabel("")
        self.meta_lbl.setObjectName("metaLabel")
        self.meta_lbl.setWordWrap(True)
        self.meta_lbl.setVisible(False)
        layout.addWidget(self.meta_lbl)

        self.warn_lbl = QLabel("")
        self.warn_lbl.setObjectName("warnLabel")
        self.warn_lbl.setWordWrap(True)
        self.warn_lbl.setVisible(False)
        layout.addWidget(self.warn_lbl)

        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        self.start = QPushButton("Start Restore")
        self.start.setObjectName("startBtn")
        self.start.setEnabled(False)
        self.start.clicked.connect(self._accept)
        br.addWidget(self.start)
        layout.addLayout(br)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Backup", "",
            f"Backup File (*{EXT_FULL} *{EXT_PART} *{DGB_EXT});;All (*)")
        if not path: return
        self.path_edit.setText(path)

        self.meta = read_dgb_meta(path)
        if self.meta:
            m = self.meta
            enc = "Encrypted | " if m.get("encrypted") else ""
            self.meta_lbl.setText(
                f"<b>{m.get('backup_type','?').upper()}</b> backup | "
                f"{enc}Source: {m.get('source_device','?')} | "
                f"Size: {m.get('source_size_human','?')} | "
                f"Compression: {m.get('compression','?')} | "
                f"Date: {m.get('created','?')[:10]}")
            self.meta_lbl.setVisible(True)
            self._validate()
            self.start.setEnabled(True)
        else:
            self.meta_lbl.setText("Cannot read metadata from this file")
            self.meta_lbl.setVisible(True)
            self.start.setEnabled(False)

    def _validate(self):
        self.warn_lbl.setVisible(False)
        if not self.meta or not self.device: return
        warns = []
        m, d = self.meta, self.device
        bt, dt = m.get("backup_type", ""), d.get("type", "")
        ss, ts = m.get("source_size", 0), d.get("size", 0)

        if bt == "disk" and dt == "part":
            warns.append("Warning: disk backup -> partition target")
        elif bt == "partition" and dt == "disk":
            warns.append("Warning: partition backup -> disk target")
        if ts > 0 and ss > 0 and ts < ss:
            warns.append(f"Warning: target ({human_size(ts)}) smaller than source ({human_size(ss)})")
        if warns:
            self.warn_lbl.setText("\n".join(warns))
            self.warn_lbl.setVisible(True)

    def _accept(self):
        if not self.meta: return
        password = None
        if self.meta.get("encrypted"):
            if not HAS_CRYPTO:
                QMessageBox.critical(self, "Error",
                    "Backup is encrypted!\n\nInstall: sudo apt install python3-cryptography")
                return
            pw, ok = QInputDialog.getText(self, "Password",
                "Enter decryption password:", QLineEdit.EchoMode.Password)
            if not ok or not pw: return
            password = pw

        d = self.device
        r1 = QMessageBox.warning(self, "Confirm",
            f"<b>ALL data on {d['name']} will be OVERWRITTEN!</b><br><br>"
            f"Type: <b>{self.meta.get('backup_type','?')}</b><br>"
            f"Source: <b>{self.meta.get('source_device','?')} ({self.meta.get('source_size_human','?')})</b><br>"
            f"Target: <b>{d['name']} ({human_size(d.get('size'))})</b><br><br>"
            f"<b>This cannot be undone!</b>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r1 != QMessageBox.StandardButton.Yes: return

        self.result_data = {
            "path": self.path_edit.text(),
            "meta": self.meta,
            "password": password,
        }
        self.accept()


class VerifyDialog(QDialog):
    """Dialog for verify settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result_data = None
        self.setWindowTitle("Verify Backup")
        self.setMinimumWidth(500)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        layout.addWidget(QLabel("Verify the integrity of a .dgb backup file.\nNo password required."))

        fr = QHBoxLayout()
        fr.addWidget(QLabel("File:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select .dgb file...")
        self.path_edit.setReadOnly(True)
        fr.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse")
        browse.setFixedWidth(80)
        browse.clicked.connect(self._browse)
        fr.addWidget(browse)
        layout.addLayout(fr)

        self.meta_lbl = QLabel("")
        self.meta_lbl.setObjectName("metaLabel")
        self.meta_lbl.setWordWrap(True)
        self.meta_lbl.setVisible(False)
        layout.addWidget(self.meta_lbl)

        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        self.start = QPushButton("Start Verify")
        self.start.setObjectName("startBtn")
        self.start.setEnabled(False)
        self.start.clicked.connect(self._accept)
        br.addWidget(self.start)
        layout.addLayout(br)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "",
            f"Backup File (*{EXT_FULL} *{EXT_PART} *{DGB_EXT});;All (*)")
        if not path: return
        self.path_edit.setText(path)
        meta = read_dgb_meta(path)
        if meta:
            enc = "Encrypted | " if meta.get("encrypted") else ""
            self.meta_lbl.setText(
                f"<b>{meta.get('backup_type','?').upper()}</b> | "
                f"{enc}{meta.get('source_device','?')} | {meta.get('source_size_human','?')} | "
                f"{meta.get('created','?')[:10]}")
            self.meta_lbl.setVisible(True)
        self.start.setEnabled(True)

    def _accept(self):
        self.result_data = {"path": self.path_edit.text()}
        self.accept()


class PipelineReader:
    """Reads from archive file, decrypts, decompresses, provides exact-sized reads."""

    def __init__(self, f, data_size, decryptor, decomp, sha):
        self.f = f
        self.data_size = data_size
        self.decryptor = decryptor
        self.decomp = decomp
        self.sha = sha
        self._buf = bytearray()
        self.file_bytes_read = 0

    def read_exact(self, n):
        """Read exactly *n* bytes from the decompressed stream."""
        while len(self._buf) < n:
            if self.file_bytes_read >= self.data_size:
                break
            csz = min(CHUNK_SIZE, self.data_size - self.file_bytes_read)
            raw = self.f.read(csz)
            if not raw:
                break
            self.file_bytes_read += len(raw)
            self.sha.update(raw)
            if self.decryptor:
                raw = self.decryptor.update(raw)
            if self.decomp:
                raw = self.decomp.decompress(raw)
            self._buf.extend(raw)
        result = bytes(self._buf[:n])
        del self._buf[:n]
        return result

    def finalize(self):
        if self.decryptor:
            tail = self.decryptor.finalize()
            if tail:
                if self.decomp:
                    try: tail = self.decomp.decompress(tail)
                    except Exception: pass
                self._buf.extend(tail)


class BackupWorker(QThread):
    progress = Signal(int, str)
    log_msg = Signal(str)
    finished_sig = Signal(bool, str)

    def __init__(self, op, source, dest, compression="none",
                 password=None, metadata=None, parent=None):
        super().__init__(parent)
        self.op = op
        self.source = source
        self.dest = dest
        self.compression = compression
        self.password = password
        self.metadata = metadata or {}
        self._cancelled = False
        self._process = None

    def cancel(self):
        self._cancelled = True
        if self._process and self._process.poll() is None:
            try: os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            except: pass

    def run(self):
        try:
            {"backup": self._backup, "restore": self._restore,
             "verify": self._verify}[self.op]()
        except Exception as e:
            self.finished_sig.emit(False, f"Error: {e}")

    def _dev_size(self, dev):
        try:
            r = subprocess.run(["blockdev", "--getsize64", dev],
                               capture_output=True, text=True, timeout=5)
            return int(r.stdout.strip())
        except Exception:
            return 0

    @staticmethod
    def _pc_fs(fstype):
        """Return partclone filesystem name or None."""
        return PARTCLONE_FS.get((fstype or "").lower())

    @staticmethod
    def _pc_available(fs):
        return shutil.which(f"partclone.{fs}") is not None

    def _disk_partitions(self, device):
        """Return child partitions of *device* via lsblk."""
        try:
            r = subprocess.run(
                ["lsblk", "-Jbpo", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT"],
                capture_output=True, text=True, timeout=10)
            self.log_msg.emit(f"  lsblk returncode: {r.returncode}")
            for d in json.loads(r.stdout).get("blockdevices", []):
                if d.get("name") == device:
                    children = [c for c in (d.get("children") or [])
                                if c.get("type") in ("part", "lvm", "crypt")]
                    self.log_msg.emit(f"  Gefundene Partitionen: {len(children)}")
                    for c in children:
                        self.log_msg.emit(f"    {c.get('name')} | {c.get('fstype','?')} | "
                                          f"{human_size(c.get('size',0))} | "
                                          f"mount={c.get('mountpoint','-')}")
                    return children
        except Exception as e:
            self.log_msg.emit(f"  Warning: lsblk error: {e}")
        return []

    def _unmount(self, device):
        """Unmount a device if mounted. Required for partclone."""
        try:
            r = subprocess.run(["findmnt", "-n", "-o", "TARGET", device],
                               capture_output=True, text=True, timeout=5)
            mp = r.stdout.strip()
            if mp:
                self.log_msg.emit(f"  Unmounting {device} ({mp})...")
                subprocess.run(["umount", device], capture_output=True, timeout=15)
                time.sleep(1)
        except Exception:
            pass

    def _used_bytes(self, device):
        """Get used bytes on a (still mounted) filesystem via df."""
        try:
            r = subprocess.run(["df", "-B1", "--output=used", device],
                               capture_output=True, text=True, timeout=5)
            lines = r.stdout.strip().splitlines()
            if len(lines) >= 2:
                val = int(lines[-1].strip())
                if val > 0:
                    return val
        except Exception:
            pass
        return 0

    @staticmethod
    def _drain_stderr(proc):
        """Drain stderr from a subprocess in a background thread.
        Returns (thread, lines_list). Call thread.join() after proc.wait()."""
        lines = []
        def _reader():
            try:
                for raw_line in proc.stderr:
                    lines.append(raw_line.decode("utf-8", errors="replace").rstrip())
            except Exception:
                pass
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        return t, lines

    def _sfdisk_dump(self, device):
        try:
            r = subprocess.run(["sfdisk", "-d", device],
                               capture_output=True, text=True, timeout=10)
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    # pipeline: raw -> compress -> encrypt -> sha -> file
    def _pw(self, raw, out, comp, enc, sha):
        data = comp.compress(raw) if comp else raw
        if data:
            if enc: data = enc.update(data)
            sha.update(data); out.write(data)

    def _pflush(self, out, comp, enc, sha):
        if comp:
            fl = comp.flush()
            if fl:
                if enc: fl = enc.update(fl)
                sha.update(fl); out.write(fl)
        if enc:
            ef = enc.finalize()
            if ef: sha.update(ef); out.write(ef)

    # segment writers (operate on raw / pre-compression layer)
    def _seg_header(self, out, comp, enc, sha, stype, name, method, orig_sz):
        nb = name.encode("utf-8")
        hdr = stype + struct.pack(">H", len(nb)) + nb
        hdr += struct.pack(">B", method) + struct.pack(">Q", orig_sz)
        self._pw(hdr, out, comp, enc, sha)

    def _seg_chunk(self, out, comp, enc, sha, data):
        self._pw(struct.pack(">I", len(data)), out, comp, enc, sha)
        self._pw(data, out, comp, enc, sha)

    def _seg_end(self, out, comp, enc, sha):
        self._pw(struct.pack(">I", 0), out, comp, enc, sha)

    def _backup(self):
        device, outpath = self.source, self.dest
        total = self._dev_size(device)
        meta = self.metadata.copy()
        is_disk = meta.get("backup_type") == "disk"
        completed = False

        self.log_msg.emit(f"Backup: {device} -> {outpath}")
        self.log_msg.emit(f"Size: {human_size(total)} | Comp: {self.compression}"
                          f" | Enc: {'yes' if self.password else 'no'}")

        try:
            with open(outpath, "wb") as out:
                out.write(MAGIC)

                if is_disk:
                    parts = self._disk_partitions(device)
                    pt_dump = self._sfdisk_dump(device)
                    meta["clone_method"] = "smart_disk"
                    meta["partition_table_text"] = pt_dump
                    meta["partition_count"] = len(parts)
                    meta["partitions_info"] = [
                        {"name": p["name"], "fstype": p.get("fstype",""),
                         "size": p.get("size",0)} for p in parts]
                    self.log_msg.emit(f"Mode: Smart Disk ({len(parts)} Partitionen)")
                else:
                    fs = meta.get("source_fstype", "").lower()
                    pcf = self._pc_fs(fs)
                    if pcf and self._pc_available(pcf):
                        meta["clone_method"] = "partclone"
                        self.log_msg.emit(f"Mode: Smart (partclone.{pcf})")
                    else:
                        meta["clone_method"] = "dd"
                        self.log_msg.emit("Mode: Raw Sektor (dd)")

                meta["compression"] = self.compression
                meta["encrypted"] = self.password is not None
                mb = json.dumps(meta, indent=2).encode()
                out.write(struct.pack(">I", len(mb))); out.write(mb)

                enc = None
                if self.password:
                    salt, iv = os.urandom(16), os.urandom(16)
                    out.write(salt); out.write(iv)
                    enc = Cipher(algorithms.AES(derive_key(self.password, salt)),
                                 modes.CTR(iv)).encryptor()
                    self.log_msg.emit("AES-256-CTR initialized")

                comp = (zlib.compressobj(1) if self.compression == "zlib"
                        else lzma.LZMACompressor(preset=1) if self.compression == "lzma"
                        else None)
                sha = hashlib.sha256()
                t0 = time.time()

                if is_disk:
                    self._backup_disk(device, parts, pt_dump, out, comp, enc, sha, total, t0)
                else:
                    self._backup_part(device, meta, out, comp, enc, sha, total, t0)

                if self._cancelled:
                    return

                self._pflush(out, comp, enc, sha)
                out.write(sha.digest())

            completed = True
            el = time.time() - t0
            fsize = os.path.getsize(outpath)
            ratio = f"{(fsize/total*100):.0f}%" if total else "\u2014"
            self.progress.emit(100, "Complete!")
            self.log_msg.emit(f"SHA256: {sha.hexdigest()}")
            self.log_msg.emit(f"Size: {human_size(fsize)} ({ratio}) | "
                              f"Time: {int(el//60)}m{int(el%60):02d}s")
            self.finished_sig.emit(True,
                f"Backup complete!\n\n{outpath}\n{human_size(fsize)} ({ratio})"
                f"\nTime: {int(el//60)}m{int(el%60):02d}s")

        finally:
            # Clean up incomplete backup file on cancel/error
            if not completed and os.path.exists(outpath):
                try:
                    os.remove(outpath)
                    self.log_msg.emit(f"Unvollst\u00e4ndige Datei gel\u00f6scht: {outpath}")
                except Exception as e:
                    self.log_msg.emit(f"\u26a0 Konnte Datei nicht l\u00f6schen: {e}")

    def _backup_part(self, device, meta, out, comp, enc, sha, total, t0):
        cm = meta.get("clone_method", "dd")
        pcf = self._pc_fs(meta.get("source_fstype", ""))

        used = self._used_bytes(device) if cm == "partclone" else 0

        self._unmount(device)

        if cm == "partclone" and pcf:
            cmd = [f"partclone.{pcf}", "-c", "-s", device, "-o", "-"]
        else:
            cmd = ["dd", f"if={device}", "bs=4M", "status=none"]

        progress_total = used if used > 0 else total
        self.log_msg.emit(f"CMD: {' '.join(cmd)}")
        if used > 0:
            self.log_msg.emit(f"  Belegte Daten: {human_size(used)} (Fortschritt-Basis)")

        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                preexec_fn=os.setsid)
        except FileNotFoundError:
            self.finished_sig.emit(False,
                f"Befehl nicht gefunden: {cmd[0]}\n"
                f"Bitte installieren: sudo apt install partclone")
            return
        except Exception as e:
            self.finished_sig.emit(False, f"Prozess-Start fehlgeschlagen: {e}")
            return

        # Drain stderr in background thread to prevent pipe deadlock
        stderr_thread, stderr_lines = self._drain_stderr(self._process)

        read = 0
        while True:
            if self._cancelled:
                self._process.terminate()
                self.finished_sig.emit(False, "Cancelled"); return
            raw = self._process.stdout.read(CHUNK_SIZE)
            if not raw: break
            read += len(raw)
            self._pw(raw, out, comp, enc, sha)
            if progress_total > 0:
                pct = min(int((read / progress_total) * 99), 99)
                el = time.time() - t0
                spd = read / el if el > 0 else 0
                self.progress.emit(pct,
                    f"{pct}% | {human_size(read)} / {human_size(progress_total)} | {human_size(spd)}/s")
        self._process.wait()
        stderr_thread.join(timeout=5)

        rc = self._process.returncode
        if stderr_lines:
            for line in stderr_lines[-5:]:
                self.log_msg.emit(f"  stderr: {line}")
        self.log_msg.emit(f"  Gelesen: {human_size(read)} | returncode: {rc}")

        if read == 0:
            self.finished_sig.emit(False,
                f"Keine Daten gelesen von {device}!\n\n"
                f"Befehl: {' '.join(cmd)}\n"
                f"Return-Code: {rc}\n"
                f"{chr(10).join(stderr_lines[-5:]) if stderr_lines else 'Kein stderr'}")
            return

        if rc != 0:
            self.log_msg.emit(f"  Process ended with code {rc}")

    def _backup_disk(self, device, parts, pt_dump, out, comp, enc, sha, total, t0):
        total_used = 0
        for p in parts:
            u = self._used_bytes(p["name"])
            total_used += u if u > 0 else p.get("size", 0)
        self.log_msg.emit(f"  Geschaetzte Datenmenge: {human_size(total_used)}")
        done = 0

        if not parts:
            self.finished_sig.emit(False,
                f"Keine Partitionen auf {device} gefunden!\n\n"
                f"lsblk zeigt keine Kinder fuer dieses Geraet.\n"
                f"Evtl. falsches Geraet gewaehlt?")
            return

        self.log_msg.emit("Saving partition table...")
        if not pt_dump:
            self.log_msg.emit("  Warning: sfdisk returned no data")
        pt_b = pt_dump.encode("utf-8")
        self._seg_header(out, comp, enc, sha, SEG_PTABLE, "ptable", 0, len(pt_b))
        self._seg_chunk(out, comp, enc, sha, pt_b)
        self._seg_end(out, comp, enc, sha)

        self.log_msg.emit("Saving boot sector (1 MB)...")
        try:
            with open(device, "rb") as df:
                boot = df.read(1024 * 1024)
            self.log_msg.emit(f"  Boot-Sektor: {human_size(len(boot))}")
        except Exception as e:
            boot = b""
            self.log_msg.emit(f"  Warning: boot sector error: {e}")
        self._seg_header(out, comp, enc, sha, SEG_BOOT, "boot", 0, len(boot))
        self._seg_chunk(out, comp, enc, sha, boot)
        self._seg_end(out, comp, enc, sha)

        for i, p in enumerate(parts):
            if self._cancelled:
                self.finished_sig.emit(False, "Cancelled"); return
            pname = p["name"]
            pfs   = (p.get("fstype") or "").lower()
            psz   = p.get("size", 0)
            seg   = os.path.basename(pname)
            pcf   = self._pc_fs(pfs)

            self._unmount(pname)

            if pcf and self._pc_available(pcf):
                cmd = [f"partclone.{pcf}", "-c", "-s", pname, "-o", "-"]
                method = METHOD_PARTCLONE
                mstr = f"partclone.{pcf}"
            else:
                cmd = ["dd", f"if={pname}", "bs=4M", "status=none"]
                method = METHOD_DD
                mstr = "dd"

            self.log_msg.emit(f"Partition {i+1}/{len(parts)}: {seg} "
                              f"({pfs or '?'}, {human_size(psz)}) [{mstr}]")
            self.log_msg.emit(f"  CMD: {' '.join(cmd)}")
            self._seg_header(out, comp, enc, sha, SEG_PART, seg, method, psz)

            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    preexec_fn=os.setsid)
            except FileNotFoundError:
                self.log_msg.emit(f"  Warning: {cmd[0]} not found, skipping")
                self._seg_end(out, comp, enc, sha)
                continue
            except Exception as e:
                self.log_msg.emit(f"  Warning: start failed: {e}")
                self._seg_end(out, comp, enc, sha)
                continue

            stderr_thread, stderr_lines = self._drain_stderr(proc)

            self._process = proc
            pread = 0
            while True:
                if self._cancelled:
                    proc.terminate()
                    self.finished_sig.emit(False, "Cancelled"); return
                raw = proc.stdout.read(CHUNK_SIZE)
                if not raw: break
                pread += len(raw); done += len(raw)
                self._seg_chunk(out, comp, enc, sha, raw)
                if total_used > 0:
                    pct = min(int((done / total_used) * 95), 95)
                    el = time.time() - t0
                    spd = done / el if el > 0 else 0
                    self.progress.emit(pct,
                        f"{pct}% | {human_size(done)} / {human_size(total_used)} | {human_size(spd)}/s")
            self._seg_end(out, comp, enc, sha)
            proc.wait()
            stderr_thread.join(timeout=5)

            rc = proc.returncode
            if stderr_lines:
                for line in stderr_lines[-5:]:
                    self.log_msg.emit(f"  stderr: {line}")
            if pread == 0:
                self.log_msg.emit(f"  Warning: 0 bytes read from {pname} "
                                  f"rc={rc}")
            else:
                self.log_msg.emit(f"  OK: {seg}: {human_size(pread)} (rc={rc})")

        self._seg_header(out, comp, enc, sha, SEG_END, "", 0, 0)

    def _restore(self):
        container, device = self.source, self.dest
        self.log_msg.emit(f"Restore: {container} -> {device}")

        with open(container, "rb") as inf:
            if inf.read(4) != MAGIC:
                self.finished_sig.emit(False, "Not a valid backup file!"); return
            ml = struct.unpack(">I", inf.read(4))[0]
            fm = json.loads(inf.read(ml))

            dec = None
            if fm.get("encrypted"):
                salt, iv = inf.read(16), inf.read(16)
                dec = Cipher(algorithms.AES(derive_key(self.password, salt)),
                             modes.CTR(iv)).decryptor()

            ds = inf.tell()
            inf.seek(-HASH_SIZE, 2); de = inf.tell()
            expected = inf.read(HASH_SIZE)
            inf.seek(ds)
            dsize = de - ds

            decomp = (zlib.decompressobj() if fm.get("compression") == "zlib"
                      else lzma.LZMADecompressor() if fm.get("compression") == "lzma"
                      else None)

            cm = fm.get("clone_method", "dd")
            t0 = time.time()

            if cm == "smart_disk":
                self._restore_disk(inf, fm, device, dec, decomp, dsize, expected, t0)
            else:
                self._restore_part(inf, fm, device, dec, decomp, dsize, expected, t0)

    def _restore_part(self, inf, fm, device, dec, decomp, dsize, expected, t0):
        fstype = fm.get("source_fstype", "").lower()
        pcf = self._pc_fs(fstype)
        cm = fm.get("clone_method", "dd")
        if cm == "partclone" and pcf:
            cmd = [f"partclone.{pcf}", "-r", "-s", "-", "-o", device, "-q"]
            self.log_msg.emit(f"Mode: Smart (partclone.{pcf})")
        else:
            cmd = ["dd", f"of={device}", "bs=4M", "status=none", "conv=fsync"]
            self.log_msg.emit("Mode: Raw Sektor (dd)")

        self._process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=os.setsid)
        sha = hashlib.sha256()
        read = 0
        while read < dsize:
            if self._cancelled:
                self._process.terminate()
                self.finished_sig.emit(False, "Cancelled"); return
            chunk = inf.read(min(CHUNK_SIZE, dsize - read))
            if not chunk: break
            read += len(chunk); sha.update(chunk)
            if dec: chunk = dec.update(chunk)
            if decomp:
                try: chunk = decomp.decompress(chunk)
                except Exception as e:
                    self._process.terminate()
                    self.finished_sig.emit(False,
                        f"Decompression failed - wrong password?\n{e}"); return
            if chunk:
                try: self._process.stdin.write(chunk)
                except BrokenPipeError:
                    self.finished_sig.emit(False, "Write error"); return
            pct = int((read / dsize) * 100) if dsize else 0
            el = time.time() - t0
            spd = read / el if el > 0 else 0
            eta = (dsize - read) / spd if spd > 0 else 0
            self.progress.emit(pct,
                f"{pct}% | {human_size(spd)}/s | "
                f"ETA {int(eta//60)}:{int(eta%60):02d}")
        if dec:
            tail = dec.finalize()
            if tail:
                if decomp:
                    try: tail = decomp.decompress(tail)
                    except Exception: pass
                if tail:
                    try: self._process.stdin.write(tail)
                    except Exception: pass
        self._process.stdin.close(); self._process.wait()
        el = time.time() - t0
        ok = sha.digest() == expected
        self.progress.emit(100, "Complete!")
        self.log_msg.emit(f"Integrity: {'OK' if ok else 'MISMATCH'} | "
                          f"Time: {int(el//60)}m{int(el%60):02d}s")
        self.finished_sig.emit(True,
            f"Restore complete!\n\nTarget: {device}\n"
            f"Integrity: {'OK' if ok else 'MISMATCH'}\n"
            f"Time: {int(el//60)}m{int(el%60):02d}s")

    def _restore_disk(self, inf, fm, device, dec, decomp, dsize, expected, t0):
        self.log_msg.emit("Mode: Smart Disk Restore")
        sha = hashlib.sha256()
        reader = PipelineReader(inf, dsize, dec, decomp, sha)
        restored = 0
        total_est = sum(p.get("size", 0) for p in fm.get("partitions_info", []))

        while True:
            if self._cancelled:
                self.finished_sig.emit(False, "Cancelled"); return

            stype = reader.read_exact(4)
            if len(stype) < 4 or stype == SEG_END:
                break

            nlen = struct.unpack(">H", reader.read_exact(2))[0]
            name = reader.read_exact(nlen).decode("utf-8", errors="replace") if nlen else ""
            method = struct.unpack(">B", reader.read_exact(1))[0]
            orig_sz = struct.unpack(">Q", reader.read_exact(8))[0]

            if stype == SEG_PTABLE:
                self.log_msg.emit("Restoring partition table...")
                pt_data = self._read_seg_data(reader)
                try:
                    proc = subprocess.Popen(
                        ["sfdisk", "--force", device],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
                    proc.communicate(input=pt_data, timeout=30)
                    self.log_msg.emit("  Partition table restored")
                except Exception as e:
                    self.log_msg.emit(f"  Warning: sfdisk error: {e}")
                subprocess.run(["partprobe", device],
                               capture_output=True, timeout=10)
                time.sleep(2)

            elif stype == SEG_BOOT:
                self.log_msg.emit("Restoring boot sector...")
                boot_data = self._read_seg_data(reader)
                try:
                    with open(device, "r+b") as df:
                        df.write(boot_data)
                        df.flush(); os.fsync(df.fileno())
                    self.log_msg.emit(f"  Boot sector: {human_size(len(boot_data))}")
                except Exception as e:
                    self.log_msg.emit(f"  Warning: boot sector error: {e}")

            elif stype == SEG_PART:
                part_dev = self._resolve_part_dev(device, name)
                self.log_msg.emit(f"Partition: {name} -> {part_dev} "
                                  f"({human_size(orig_sz)})")

                pcf = None
                if method == METHOD_PARTCLONE:
                    for pi in fm.get("partitions_info", []):
                        if os.path.basename(pi.get("name","")) == name:
                            pcf = self._pc_fs(pi.get("fstype",""))
                            break
                if method == METHOD_PARTCLONE and pcf:
                    cmd = [f"partclone.{pcf}", "-r", "-s", "-",
                           "-o", part_dev, "-q"]
                else:
                    cmd = ["dd", f"of={part_dev}", "bs=4M",
                           "status=none", "conv=fsync"]

                try:
                    proc = subprocess.Popen(
                        cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                        preexec_fn=os.setsid)
                except Exception as e:
                    self.log_msg.emit(f"  Warning: process start failed: {e} - skipping partition")
                    # Drain remaining segment data to keep stream aligned
                    while True:
                        clen_b = reader.read_exact(4)
                        if len(clen_b) < 4: break
                        clen = struct.unpack(">I", clen_b)[0]
                        if clen == 0: break
                        reader.read_exact(clen)
                    continue
                self._process = proc
                pwritten = 0
                pipe_broken = False
                while True:
                    if self._cancelled:
                        proc.terminate()
                        self.finished_sig.emit(False, "Cancelled"); return
                    clen_b = reader.read_exact(4)
                    if len(clen_b) < 4: break
                    clen = struct.unpack(">I", clen_b)[0]
                    if clen == 0: break  # end of segment
                    cdata = reader.read_exact(clen)
                    if pipe_broken:
                        # Still consume data to keep stream aligned,
                        # but don't write to the (closed) process
                        restored += len(cdata)
                        continue
                    try:
                        proc.stdin.write(cdata)
                    except BrokenPipeError:
                        self.log_msg.emit(f"  Warning: write error on {part_dev} - skipping remaining data")
                        pipe_broken = True
                        restored += len(cdata)
                        continue
                    pwritten += len(cdata); restored += len(cdata)
                    if total_est > 0:
                        pct = min(int((restored / total_est) * 95), 95)
                        el = time.time() - t0
                        spd = restored / el if el > 0 else 0
                        eta = (total_est - restored) / spd if spd > 0 else 0
                        self.progress.emit(pct,
                            f"{pct}% | {human_size(spd)}/s | "
                            f"ETA {int(eta//60)}:{int(eta%60):02d}")
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                proc.wait()
                self.log_msg.emit(f"  OK: {name}: {human_size(pwritten)}")
            else:
                # unknown segment type, skip its data
                self._read_seg_data(reader)

        reader.finalize()
        el = time.time() - t0
        ok = sha.digest() == expected
        self.progress.emit(100, "Complete!")
        self.log_msg.emit(f"Integrity: {'OK' if ok else 'MISMATCH'} | "
                          f"Time: {int(el//60)}m{int(el%60):02d}s")
        self.finished_sig.emit(True,
            f"Disk Restore complete!\n\nTarget: {device}\n"
            f"Integrity: {'OK' if ok else 'MISMATCH'}\n"
            f"Time: {int(el//60)}m{int(el%60):02d}s")

    def _read_seg_data(self, reader):
        """Read all chunks of a segment, return concatenated bytes."""
        buf = bytearray()
        while True:
            lb = reader.read_exact(4)
            if len(lb) < 4: break
            ln = struct.unpack(">I", lb)[0]
            if ln == 0: break
            buf.extend(reader.read_exact(ln))
        return bytes(buf)

    @staticmethod
    def _resolve_part_dev(disk, part_name):
        """Resolve e.g. disk=/dev/sda  part_name=sda1 -> /dev/sda1"""
        candidate = os.path.join(os.path.dirname(disk), part_name)
        if os.path.exists(candidate):
            return candidate
        # NVMe style:  /dev/nvme0n1  +  nvme0n1p1
        if os.path.exists(disk + "p" + part_name.split("p")[-1]):
            return disk + "p" + part_name.split("p")[-1]
        return candidate  # best guess

    def _verify(self):
        fp = self.source
        self.log_msg.emit(f"Verifying: {fp}")

        with open(fp, "rb") as f:
            if f.read(4) != MAGIC:
                self.finished_sig.emit(False, "Not a valid backup file!"); return
            ml = struct.unpack(">I", f.read(4))[0]
            meta = json.loads(f.read(ml))
            cm = meta.get('clone_method', '?')
            self.log_msg.emit(
                f"{meta.get('backup_type','?').upper()} | "
                f"{meta.get('source_device','?')} | "
                f"{meta.get('source_size_human','?')} | {cm}")
            if meta.get("encrypted"): f.read(32)
            ds = f.tell()
            f.seek(-HASH_SIZE, 2); de = f.tell()
            expected = f.read(HASH_SIZE)
            f.seek(ds)
            dsize = de - ds

            sha = hashlib.sha256()
            read, t0 = 0, time.time()
            while read < dsize:
                if self._cancelled:
                    self.finished_sig.emit(False, "Cancelled"); return
                chunk = f.read(min(CHUNK_SIZE, dsize - read))
                if not chunk: break
                sha.update(chunk); read += len(chunk)
                pct = int((read / dsize) * 100) if dsize else 0
                el = time.time() - t0
                spd = read / el if el > 0 else 0
                self.progress.emit(pct, f"{pct}% | {human_size(spd)}/s")

            ok = sha.digest() == expected
            self.progress.emit(100, "Verified" if ok else "Failed")
            self.log_msg.emit(f"SHA256: {sha.hexdigest()}")
            if ok:
                self.finished_sig.emit(True,
                    f"Integrity OK\n\nSHA256: {sha.hexdigest()[:32]}...")
            else:
                self.finished_sig.emit(False, "File is corrupted.")


class SystemBUWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.selected_device = None
        self.log_visible = False

        self.setWindowTitle("SystemBU")
        self.setMinimumSize(860, 520)
        self.resize(960, 600)
        self._center_window()
        self._build_ui()
        self._refresh()

    def _center_window(self):
        screen = QApplication.primaryScreen().geometry()
        window_size = self.geometry()
        x = (screen.width() - window_size.width()) // 2
        y = (screen.height() - window_size.height()) // 2
        self.move(x, y)

    def _build_ui(self):
        c = QWidget()
        self.setCentralWidget(c)
        ml = QVBoxLayout(c)
        ml.setContentsMargins(16, 12, 16, 12)
        ml.setSpacing(10)

        hl = QHBoxLayout()
        t = QLabel("SystemBU")
        t.setObjectName("titleLabel")
        hl.addWidget(t)
        hl.addStretch()
        self.status = QLabel("Ready")
        self.status.setObjectName("statusLabel")
        hl.addWidget(self.status)
        ml.addLayout(hl)

        sep = QFrame(); sep.setObjectName("separator"); sep.setFrameShape(QFrame.Shape.HLine)
        ml.addWidget(sep)

        ab = QHBoxLayout()
        self.btn_full = QPushButton("Full Backup")
        self.btn_full.setObjectName("actionBtn")
        self.btn_full.setToolTip("Ganze Festplatte sichern (Smart-Disk)")
        self.btn_full.clicked.connect(lambda: self._do_backup("disk"))
        self.btn_part = QPushButton("Part Backup")
        self.btn_part.setObjectName("actionBtn")
        self.btn_part.setToolTip("Einzelne Partition sichern")
        self.btn_part.clicked.connect(lambda: self._do_backup("partition"))
        self.btn_restore = QPushButton("Restore")
        self.btn_restore.setObjectName("actionBtn")
        self.btn_restore.clicked.connect(self._do_restore)
        self.btn_verify = QPushButton("Verify")
        self.btn_verify.setObjectName("actionBtn")
        self.btn_verify.clicked.connect(self._do_verify)
        ab.addWidget(self.btn_full)
        ab.addWidget(self.btn_part)
        ab.addWidget(self.btn_restore)
        ab.addWidget(self.btn_verify)
        ab.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setVisible(False)
        ab.addWidget(self.cancel_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("actionBtn")
        self.refresh_btn.setMinimumWidth(100)
        self.refresh_btn.clicked.connect(self._refresh)
        ab.addWidget(self.refresh_btn)
        ml.addLayout(ab)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Model", "Device", "Size", "Used", "Type", "FS", "Mount", "Label"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_sel)
        hv = self.tree.header()
        hv.setStretchLastSection(True)
        for i in range(2, 8):
            hv.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 140)
        ml.addWidget(self.tree, 1)

        self.pbar = DualProgressBar()
        ml.addWidget(self.pbar)

        self.log_toggle = QPushButton("Show Log")
        self.log_toggle.setObjectName("logToggle")
        self.log_toggle.clicked.connect(self._toggle_log)
        ml.addWidget(self.log_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(0)
        self.log_box.setMaximumHeight(0)
        ml.addWidget(self.log_box)

    def _refresh(self):
        self.tree.clear()
        self.selected_device = None
        self._update_btns()

        devs = get_block_devices()
        self.usage_map = get_all_usage(devs)

        system_disks = set()
        for d in devs:
            for ch in (d.get("children") or []):
                mp = ch.get("mountpoint", "") or ""
                fs = (ch.get("fstype") or "").lower()
                if mp in ("/", "/run/live/medium", "/cdrom") or fs == "squashfs":
                    system_disks.add(d.get("name", ""))

        nd, np = 0, 0
        for d in devs:
            if d.get("type") not in ("disk", "loop"): continue
            if d.get("type") == "loop" and d.get("size", 0) == 0: continue
            if d.get("name", "") in system_disks: continue
            if (d.get("fstype") or "").lower() == "squashfs": continue
            nd += 1
            ro = "RO " if d.get("ro") else ""
            dtype = disk_type_label(d)
            model = (d.get("model") or "").strip()
            vendor = (d.get("vendor") or "").strip()
            serial = (d.get("serial") or "").strip()
            tran = (d.get("tran") or "").upper()
            desc = " ".join(filter(None, [vendor, model]))
            if serial: desc += f"  [{serial[:12]}]"
            disk_used = 0
            disk_total = 0
            disk_found = False
            for ch in (d.get("children") or []):
                cu_pct, cu_bytes, cu_total = get_disk_usage_from_map(ch, self.usage_map)
                if cu_pct is not None:
                    disk_used += cu_bytes
                    disk_total += cu_total
                    disk_found = True
            if disk_found and disk_total > 0:
                usage_pct = round((disk_used / disk_total) * 100, 1)
                used_str = f"{human_size(disk_used)} ({usage_pct}%)"
            else:
                used_str = "-"
            item = QTreeWidgetItem([
                desc,
                f"{ro}{d['name']}", human_size(d.get("size")),
                used_str,
                dtype, display_fstype(d),
                d.get("mountpoint") or "-", d.get("label") or "-"])
            item.setData(0, Qt.ItemDataRole.UserRole, d)
            f = item.font(0); f.setBold(True); item.setFont(0, f)
            item.setForeground(0, QColor("#00d4aa"))

            for ch in (d.get("children") or []):
                if ch.get("type") not in ("part", "lvm", "crypt"): continue
                if (ch.get("fstype") or "").lower() == "squashfs": continue
                np += 1
                cro = "RO " if ch.get("ro") else ""
                cu_pct, cu_bytes, _ = get_disk_usage_from_map(ch, self.usage_map)
                if cu_pct is not None:
                    cu_str = f"{human_size(cu_bytes)} ({cu_pct}%)"
                else:
                    cu_str = "-"
                ci = QTreeWidgetItem([
                    "",
                    f"  {cro}{ch['name']}", human_size(ch.get("size")),
                    cu_str,
                    ch.get("type", ""), display_fstype(ch),
                    ch.get("mountpoint") or "-", ch.get("label") or "-"])
                ci.setData(0, Qt.ItemDataRole.UserRole, ch)
                item.addChild(ci)
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)

        self._log(f"Found {nd} disks, {np} partitions | usage entries: {len(self.usage_map)}")

    def _get_device_usage(self, dev):
        """Get usage for a device. For disks, sum all children."""
        if not dev:
            return (None, 0, 0)
        dtype = dev.get("type", "")
        if dtype in ("disk", "loop"):
            total_used = 0
            total_size = 0
            found_any = False
            for ch in (dev.get("children") or []):
                pct, used, size = get_disk_usage_from_map(ch, self.usage_map)
                if pct is not None:
                    total_used += used
                    total_size += size
                    found_any = True
                else:
                    total_size += ch.get("size", 0)
            if found_any and total_size > 0:
                return (round((total_used / total_size) * 100, 1), total_used, total_size)
            return (None, 0, dev.get("size", 0))
        else:
            return get_disk_usage_from_map(dev, self.usage_map)

    def _on_sel(self):
        items = self.tree.selectedItems()
        self.selected_device = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        self._update_btns()
        self.pbar.clear_progress()
        if self.selected_device:
            pct, used, total = self._get_device_usage(self.selected_device)
            name = os.path.basename(self.selected_device.get("name", ""))
            if pct is not None:
                self.pbar.set_usage(pct,
                    f"{name}:  {human_size(used)} / {human_size(total)}  ({pct}%)")
            else:
                self.pbar.set_usage(0, f"{name}: usage unknown")
        else:
            self.pbar.reset_all()

    def _update_btns(self):
        dev = self.selected_device
        is_disk = dev is not None and dev.get("type") in ("disk", "loop")
        is_part = dev is not None and dev.get("type") in ("part", "lvm", "crypt")
        self.btn_full.setEnabled(is_disk)
        self.btn_part.setEnabled(is_part)
        self.btn_restore.setEnabled(dev is not None)
        self.btn_verify.setEnabled(True)

    def _do_backup(self, backup_type):
        if not self.selected_device: return
        dlg = BackupDialog(self.selected_device, self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        r = dlg.result_data

        dev = self.selected_device
        bt = backup_type  # "disk" or "partition"
        meta = {
            "systembu_version": "4.0",
            "created": datetime.now().isoformat(),
            "backup_type": bt,
            "source_device": dev.get("name", ""),
            "source_size": dev.get("size", 0),
            "source_size_human": human_size(dev.get("size")),
            "source_model": (dev.get("model") or "").strip(),
            "source_serial": (dev.get("serial") or "").strip(),
            "source_fstype": dev.get("fstype") or "",
            "source_label": dev.get("label") or "",
            "source_uuid": dev.get("uuid") or "",
            "source_type": dev.get("type", ""),
        }

        self.worker = BackupWorker("backup", dev["name"], r["path"],
                                   r["compression"], r["password"], meta)
        self._start_worker()

    def _do_restore(self):
        if not self.selected_device: return
        dlg = RestoreDialog(self.selected_device, self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        r = dlg.result_data

        self.worker = BackupWorker("restore", r["path"], self.selected_device["name"],
                                   password=r["password"], metadata=r["meta"])
        self._start_worker()

    def _do_verify(self):
        dlg = VerifyDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        r = dlg.result_data

        self.worker = BackupWorker("verify", r["path"], "")
        self._start_worker()

    def _start_worker(self):
        self.worker.progress.connect(self._on_progress)
        self.worker.log_msg.connect(self._log)
        self.worker.finished_sig.connect(self._on_done)
        self._set_running(True)
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, "Cancel", "Cancel?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                self.worker.cancel()

    def _set_running(self, on):
        for b in [self.btn_full, self.btn_part, self.btn_restore,
                  self.btn_verify, self.refresh_btn, self.tree]:
            b.setEnabled(not on)
        self.cancel_btn.setVisible(on)
        if on:
            self.pbar.set_progress(0, "Starting...")
            self.status.setText("Running..."); self.status.setStyleSheet("color: #ffa500;")
        else:
            self.pbar.clear_progress()
            self.status.setText("Ready"); self.status.setStyleSheet("color: #00d4aa;")
            self._update_btns()

    def _on_progress(self, pct, txt):
        self.pbar.set_progress(pct, txt)

    def _on_done(self, ok, msg):
        self._set_running(False)
        if ok:
            self.pbar.set_progress(100, "Complete")
            self._log(f"OK: {msg}")
            QMessageBox.information(self, "Complete", msg)
        else:
            self.pbar.set_progress(0, "Failed")
            self.status.setText("Failed"); self.status.setStyleSheet("color: #e74c3c;")
            self._log(f"Error: {msg}")
            QMessageBox.critical(self, "Failed", msg)

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.setFixedHeight(130)
            self.log_box.setMaximumHeight(130)
            self.log_toggle.setText("Hide Log")
        else:
            self.log_box.setFixedHeight(0)
            self.log_box.setMaximumHeight(0)
            self.log_toggle.setText("Show Log")

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"<span style='color:#00d4aa'>[{ts}]</span> {msg}")
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())


def main():
    if os.geteuid() != 0:
        print("SystemBU requires root. Run: sudo python3 systembu.py")
        try:
            a = QApplication(sys.argv); a.setStyleSheet(STYLESHEET)
            QMessageBox.warning(None, "Root Required",
                "Run with:\n  sudo python3 systembu.py")
        except: pass
        sys.exit(1)

    a = QApplication(sys.argv)
    a.setApplicationName("SystemBU")
    a.setStyleSheet(STYLESHEET)
    w = SystemBUWindow()
    w.show()
    sys.exit(a.exec())

if __name__ == "__main__":
    main()
