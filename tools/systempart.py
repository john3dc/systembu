#!/usr/bin/env python3

import sys
import os
import json
import subprocess
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPushButton, QLabel, QProgressBar,
    QTextEdit, QComboBox, QCheckBox, QInputDialog, QLineEdit,
    QGroupBox, QMessageBox, QFrame, QHeaderView, QDialog,
    QSpinBox, QFormLayout, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QBrush, QPen, QFont

STYLESHEET = """
* { font-family: 'Noto Sans', 'Segoe UI', sans-serif; font-size: 13px; }
QMainWindow, QWidget { background-color: #0f0f1a; color: #c8d6e5; }
QDialog { background-color: #0f0f1a; color: #c8d6e5; }

QTreeWidget {
    background-color: #0d1b2a; border: 1px solid #1a1a3e; border-radius: 8px;
    padding: 4px; outline: none; color: #c8d6e5; font-size: 12px;
}
QTreeWidget::item { padding: 5px 4px; border-bottom: 1px solid #1a1a3e; }
QTreeWidget::item:selected { background-color: #00d4aa22; color: #00d4aa; }
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
    border-radius: 10px; padding: 10px 18px; font-size: 13px; font-weight: bold;
}
QPushButton#actionBtn:hover { background-color: #00d4aa22; border-color: #00d4aa; }
QPushButton#actionBtn:disabled { color: #333; border-color: #1a1a2e; }

QPushButton#dangerBtn {
    background-color: #16213e; color: #e74c3c; border: 2px solid #e74c3c44;
    border-radius: 10px; padding: 10px 18px; font-size: 13px; font-weight: bold;
}
QPushButton#dangerBtn:hover { background-color: #e74c3c22; border-color: #e74c3c; }
QPushButton#dangerBtn:disabled { color: #333; border-color: #1a1a2e; }

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
    selection-background-color: #00d4aa33; selection-color: #00d4aa;
}

QSpinBox {
    background-color: #1a1a3e; color: #c8d6e5; border: 1px solid #2a2a5e;
    border-radius: 8px; padding: 6px 12px; min-height: 24px;
}
QSpinBox:hover { border-color: #00d4aa; }

QTextEdit {
    background-color: #0a0a18; color: #7f8fa6; border: 1px solid #1a1a3e;
    border-radius: 8px; padding: 8px; font-family: 'Fira Code', monospace; font-size: 11px;
}

QLabel { color: #c8d6e5; background: transparent; }
QLabel#titleLabel { font-size: 20px; font-weight: bold; color: #00d4aa; }
QLabel#subtitleLabel { font-size: 11px; color: #576574; }
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

def run_cmd(cmd, timeout=30):
    """Run a command and return (success, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)

def get_block_devices():
    try:
        r = subprocess.run(["lsblk", "-Jbpo",
            "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,LABEL,UUID,RO,TRAN,ROTA,VENDOR"],
            capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout).get("blockdevices", []) if r.returncode == 0 else []
    except: return []

def get_parted_info(device):
    """Get partition table info using parted."""
    ok, out, _ = run_cmd(["parted", "-s", device, "unit", "B", "print", "free"])
    return out if ok else ""

def disk_type_label(dev):
    tran = (dev.get("tran") or "").lower()
    rota = dev.get("rota")
    name = dev.get("name", "")
    if "nvme" in name: return "NVMe"
    if tran == "usb": return "USB"
    if rota is False or rota == "0" or rota == 0: return "SSD"
    if rota is True or rota == "1" or rota == 1: return "HDD"
    return tran.upper() if tran else "-"

def get_disk_usage(mp):
    if not mp or not os.path.ismount(mp): return None
    try:
        st = os.statvfs(mp)
        t = st.f_blocks * st.f_frsize
        f = st.f_bfree * st.f_frsize
        return round(((t - f) / t) * 100, 1) if t else None
    except: return None


FS_COLORS = {
    "ext4": "#00d4aa", "ext3": "#00b894", "ext2": "#009974",
    "xfs": "#0984e3", "btrfs": "#6c5ce7",
    "vfat": "#fdcb6e", "fat32": "#fdcb6e", "fat16": "#f9ca24",
    "ntfs": "#00b4ff", "swap": "#e17055",
    "linux-swap(v1)": "#e17055",
    "free": "#2d3436", "": "#555",
}

class DiskMapWidget(QWidget):
    """Visual representation of a disk's partition layout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.disk_size = 0
        self.partitions = []  # [(start, size, name, fstype, label)]
        self.setMinimumHeight(60)
        self.setMaximumHeight(60)

    def set_disk(self, disk_size, partitions):
        self.disk_size = disk_size
        self.partitions = partitions
        self.update()

    def paintEvent(self, event):
        if not self.disk_size or not self.partitions:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 4, self.height() - 20
        x0, y0 = 2, 2

        p.setBrush(QBrush(QColor("#16213e")))
        p.setPen(QPen(QColor("#1a1a3e"), 1))
        p.drawRoundedRect(x0, y0, w, h, 6, 6)

        for start, size, name, fstype, label in self.partitions:
            if self.disk_size <= 0: continue
            px = x0 + int((start / self.disk_size) * w)
            pw = max(2, int((size / self.disk_size) * w))

            color = FS_COLORS.get(fstype, FS_COLORS.get("", "#555"))
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(QColor("#0f0f1a"), 1))
            p.drawRoundedRect(px, y0 + 1, pw - 1, h - 2, 4, 4)

            if pw > 50:
                p.setPen(QPen(QColor("#0f0f1a" if fstype != "free" else "#888")))
                p.setFont(QFont("Noto Sans", 8, QFont.Weight.Bold))
                display = label or os.path.basename(name) if name else fstype or "free"
                p.drawText(px + 4, y0 + 1, pw - 8, h - 2,
                           Qt.AlignmentFlag.AlignCenter, display[:12])

        p.setPen(QPen(QColor("#576574")))
        p.setFont(QFont("Noto Sans", 8))
        p.drawText(x0, h + 4, w, 16, Qt.AlignmentFlag.AlignLeft,
                   f"Total: {human_size(self.disk_size)}")
        p.end()


class PartitionWorker(QThread):
    log_msg = Signal(str)
    finished_sig = Signal(bool, str)

    def __init__(self, op, args, parent=None):
        super().__init__(parent)
        self.op = op
        self.args = args

    def run(self):
        try:
            if self.op == "create_table":
                self._create_table()
            elif self.op == "create_part":
                self._create_partition()
            elif self.op == "delete_part":
                self._delete_partition()
            elif self.op == "format_part":
                self._format_partition()
            elif self.op == "mount":
                self._mount()
            elif self.op == "unmount":
                self._unmount()
        except Exception as e:
            self.finished_sig.emit(False, str(e))

    def _run(self, cmd, desc=""):
        self.log_msg.emit(f"$ {' '.join(cmd)}")
        ok, out, err = run_cmd(cmd, timeout=120)
        if out.strip(): self.log_msg.emit(out.strip())
        if not ok:
            self.log_msg.emit(f"Error: {err.strip()}")
            self.finished_sig.emit(False, f"{desc} failed:\n{err.strip()}")
        return ok

    def _create_table(self):
        dev = self.args["device"]
        table = self.args["table"]  # gpt or msdos
        if not self._run(["parted", "-s", dev, "mklabel", table],
                         f"Create {table} table"): return
        self.finished_sig.emit(True, f"Partition table '{table}' created on {dev}")

    def _create_partition(self):
        dev = self.args["device"]
        start = self.args["start"]
        end = self.args["end"]
        fstype = self.args.get("fstype", "")
        if not self._run(["parted", "-s", dev, "mkpart", "primary",
                          fstype, start, end], "Create partition"): return
        self.log_msg.emit("Updating kernel partition table...")
        run_cmd(["partprobe", dev])
        self.finished_sig.emit(True, f"Partition created on {dev}")

    def _delete_partition(self):
        dev = self.args["device"]
        num = self.args["number"]
        if not self._run(["parted", "-s", dev, "rm", str(num)],
                         f"Delete partition {num}"): return
        run_cmd(["partprobe", dev])
        self.finished_sig.emit(True, f"Partition {num} deleted from {dev}")

    def _format_partition(self):
        part = self.args["partition"]
        fstype = self.args["fstype"]
        label = self.args.get("label", "")

        if fstype == "swap":
            cmd = ["mkswap"]
            if label: cmd += ["-L", label]
            cmd.append(part)
        elif fstype in ("ext4", "ext3", "ext2"):
            cmd = ["mkfs." + fstype, "-F"]
            if label: cmd += ["-L", label]
            cmd.append(part)
        elif fstype == "xfs":
            cmd = ["mkfs.xfs", "-f"]
            if label: cmd += ["-L", label]
            cmd.append(part)
        elif fstype == "btrfs":
            cmd = ["mkfs.btrfs", "-f"]
            if label: cmd += ["-L", label]
            cmd.append(part)
        elif fstype in ("vfat", "fat32"):
            cmd = ["mkfs.vfat", "-F", "32"]
            if label: cmd += ["-n", label[:11]]
            cmd.append(part)
        elif fstype == "ntfs":
            cmd = ["mkfs.ntfs", "-f"]
            if label: cmd += ["-L", label]
            cmd.append(part)
        else:
            self.finished_sig.emit(False, f"Unsupported filesystem: {fstype}")
            return

        if not self._run(cmd, f"Format {part} as {fstype}"): return
        self.finished_sig.emit(True, f"Formatted {part} as {fstype}")

    def _mount(self):
        part = self.args["partition"]
        mount = self.args["mountpoint"]
        os.makedirs(mount, exist_ok=True)
        if not self._run(["mount", part, mount], f"Mount {part}"): return
        self.finished_sig.emit(True, f"Mounted {part} -> {mount}")

    def _unmount(self):
        part = self.args["partition"]
        if not self._run(["umount", part], f"Unmount {part}"): return
        self.finished_sig.emit(True, f"Unmounted {part}")


class CreateTableDialog(QDialog):
    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = device
        self.result_data = None
        self.setWindowTitle("Create Partition Table")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        layout.addWidget(QLabel(f"Create a new partition table on <b>{device}</b>"))

        warn = QLabel("Warning: this will destroy all existing partitions.")
        warn.setObjectName("warnLabel")
        layout.addWidget(warn)

        fl = QFormLayout()
        self.table_combo = QComboBox()
        self.table_combo.addItems(["gpt", "msdos (MBR)"])
        fl.addRow("Table type:", self.table_combo)
        layout.addLayout(fl)

        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        ok = QPushButton("Create"); ok.setObjectName("dangerBtn")
        ok.clicked.connect(self._accept); br.addWidget(ok)
        layout.addLayout(br)

    def _accept(self):
        table = self.table_combo.currentText().split(" ")[0]
        r = QMessageBox.warning(self, "Confirm",
            f"Destroy all partitions on {self.device}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self.result_data = {"device": self.device, "table": table}
            self.accept()


class CreatePartDialog(QDialog):
    def __init__(self, device, free_start_mb, free_end_mb, parent=None):
        super().__init__(parent)
        self.device = device
        self.result_data = None
        self.setWindowTitle("Create Partition")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        total_free = free_end_mb - free_start_mb
        layout.addWidget(QLabel(
            f"Create partition on <b>{device}</b><br>"
            f"Free space: {free_start_mb} MB - {free_end_mb} MB ({total_free} MB)"))

        fl = QFormLayout()

        self.start_spin = QSpinBox()
        self.start_spin.setRange(free_start_mb, free_end_mb - 1)
        self.start_spin.setValue(free_start_mb)
        self.start_spin.setSuffix(" MB")
        fl.addRow("Start:", self.start_spin)

        self.end_spin = QSpinBox()
        self.end_spin.setRange(free_start_mb + 1, free_end_mb)
        self.end_spin.setValue(free_end_mb)
        self.end_spin.setSuffix(" MB")
        fl.addRow("End:", self.end_spin)

        self.fs_combo = QComboBox()
        self.fs_combo.addItems(["ext4", "xfs", "btrfs", "vfat", "ntfs", "swap", "(none)"])
        fl.addRow("Filesystem:", self.fs_combo)

        layout.addLayout(fl)

        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        ok = QPushButton("Create"); ok.setObjectName("actionBtn")
        ok.clicked.connect(self._accept); br.addWidget(ok)
        layout.addLayout(br)

    def _accept(self):
        fs = self.fs_combo.currentText()
        if fs == "(none)": fs = ""
        self.result_data = {
            "device": self.device,
            "start": f"{self.start_spin.value()}MiB",
            "end": f"{self.end_spin.value()}MiB",
            "fstype": fs,
        }
        self.accept()


class FormatDialog(QDialog):
    def __init__(self, partition, parent=None):
        super().__init__(parent)
        self.partition = partition
        self.result_data = None
        self.setWindowTitle("Format Partition")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        layout.addWidget(QLabel(f"Format <b>{partition}</b>"))
        warn = QLabel("Warning: all data on this partition will be destroyed.")
        warn.setObjectName("warnLabel")
        layout.addWidget(warn)

        fl = QFormLayout()
        self.fs_combo = QComboBox()
        self.fs_combo.addItems(["ext4", "ext3", "xfs", "btrfs", "vfat (FAT32)", "ntfs", "swap"])
        fl.addRow("Filesystem:", self.fs_combo)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Optional volume label")
        fl.addRow("Label:", self.label_edit)
        layout.addLayout(fl)

        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        ok = QPushButton("Format"); ok.setObjectName("dangerBtn")
        ok.clicked.connect(self._accept); br.addWidget(ok)
        layout.addLayout(br)

    def _accept(self):
        r = QMessageBox.warning(self, "Confirm",
            f"Destroy all data on {self.partition}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            fs = self.fs_combo.currentText().split(" ")[0]
            self.result_data = {
                "partition": self.partition,
                "fstype": fs,
                "label": self.label_edit.text().strip(),
            }
            self.accept()


class MountDialog(QDialog):
    def __init__(self, partition, parent=None):
        super().__init__(parent)
        self.partition = partition
        self.result_data = None
        self.setWindowTitle("Mount Partition")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        layout.addWidget(QLabel(f"Mount <b>{partition}</b>"))

        fl = QFormLayout()
        self.mount_edit = QLineEdit()
        self.mount_edit.setText(f"/mnt/{os.path.basename(partition)}")
        fl.addRow("Mount point:", self.mount_edit)
        layout.addLayout(fl)

        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        ok = QPushButton("Mount"); ok.setObjectName("actionBtn")
        ok.clicked.connect(self._accept); br.addWidget(ok)
        layout.addLayout(br)

    def _accept(self):
        mp = self.mount_edit.text().strip()
        if not mp:
            QMessageBox.warning(self, "Error", "Enter a mount point!")
            return
        self.result_data = {"partition": self.partition, "mountpoint": mp}
        self.accept()


class SystemPartWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.selected_disk = None
        self.selected_part = None
        self.log_visible = False

        self.setWindowTitle("SystemPart")
        self.setMinimumSize(860, 540)
        self.resize(960, 620)
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
        t = QLabel("SystemPart")
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
        self.btn_table = QPushButton("New Table")
        self.btn_table.setObjectName("dangerBtn")
        self.btn_table.clicked.connect(self._do_new_table)
        self.btn_create = QPushButton("Create")
        self.btn_create.setObjectName("actionBtn")
        self.btn_create.clicked.connect(self._do_create)
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setObjectName("dangerBtn")
        self.btn_delete.clicked.connect(self._do_delete)
        self.btn_format = QPushButton("Format")
        self.btn_format.setObjectName("dangerBtn")
        self.btn_format.clicked.connect(self._do_format)
        self.btn_mount = QPushButton("Mount")
        self.btn_mount.setObjectName("actionBtn")
        self.btn_mount.clicked.connect(self._do_mount)
        self.btn_unmount = QPushButton("Unmount")
        self.btn_unmount.setObjectName("actionBtn")
        self.btn_unmount.clicked.connect(self._do_unmount)

        for b in [self.btn_table, self.btn_create, self.btn_delete,
                  self.btn_format, self.btn_mount, self.btn_unmount]:
            ab.addWidget(b)
        ab.addStretch()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("actionBtn")
        self.refresh_btn.setMinimumWidth(100)
        self.refresh_btn.clicked.connect(self._refresh)
        ab.addWidget(self.refresh_btn)
        ml.addLayout(ab)

        self.disk_map = DiskMapWidget()
        ml.addWidget(self.disk_map)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Device", "Size", "Type", "FS", "Mount", "Label", "Info"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_sel)
        hv = self.tree.header()
        hv.setStretchLastSection(True)
        for i in range(1, 6):
            hv.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setColumnWidth(0, 160)
        ml.addWidget(self.tree, 1)

        self.log_toggle = QPushButton("Show Log")
        self.log_toggle.setObjectName("logToggle")
        self.log_toggle.clicked.connect(self._toggle_log)
        ml.addWidget(self.log_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(0)
        self.log_box.setMaximumHeight(0)
        ml.addWidget(self.log_box)

        self._update_btns()

    def _refresh(self):
        self.tree.clear()
        self.selected_disk = None
        self.selected_part = None
        self.disk_map.set_disk(0, [])
        self._update_btns()

        devs = get_block_devices()
        for d in devs:
            if d.get("type") not in ("disk",): continue
            dtype = disk_type_label(d)
            model = (d.get("model") or "").strip()
            vendor = (d.get("vendor") or "").strip()
            serial = (d.get("serial") or "").strip()
            desc = " ".join(filter(None, [vendor, model]))
            if serial: desc += f"  [{serial[:12]}]"

            item = QTreeWidgetItem([
                d["name"], human_size(d.get("size")),
                dtype, d.get("fstype") or "-",
                d.get("mountpoint") or "-", d.get("label") or "-",
                desc or "-"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("disk", d))
            f = item.font(0); f.setBold(True); item.setFont(0, f)
            item.setForeground(0, QColor("#00d4aa"))
            type_colors = {"SSD": "#00d4aa", "NVMe": "#00b4ff", "HDD": "#ffa500", "USB": "#ff69b4"}
            item.setForeground(2, QColor(type_colors.get(dtype, "#888")))

            for ch in (d.get("children") or []):
                if ch.get("type") not in ("part", "lvm", "crypt"): continue
                cu = get_disk_usage(ch.get("mountpoint"))
                cu_str = f" ({cu}%)" if cu else ""
                ci = QTreeWidgetItem([
                    f"  {ch['name']}", human_size(ch.get("size")) + cu_str,
                    ch.get("type", ""), ch.get("fstype") or "-",
                    ch.get("mountpoint") or "-", ch.get("label") or "-",
                    "-"])
                ci.setData(0, Qt.ItemDataRole.UserRole, ("part", ch))
                item.addChild(ci)
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)

        self._log("Devices refreshed")

    def _on_sel(self):
        items = self.tree.selectedItems()
        self.selected_disk = None
        self.selected_part = None

        if items:
            data = items[0].data(0, Qt.ItemDataRole.UserRole)
            if data:
                kind, dev = data
                if kind == "disk":
                    self.selected_disk = dev
                    self._update_disk_map(dev)
                elif kind == "part":
                    self.selected_part = dev
                    parent = items[0].parent()
                    if parent:
                        pdata = parent.data(0, Qt.ItemDataRole.UserRole)
                        if pdata:
                            self.selected_disk = pdata[1]
                            self._update_disk_map(pdata[1])
        self._update_btns()

    def _update_disk_map(self, disk):
        """Update the visual disk map for the selected disk."""
        disk_size = disk.get("size", 0)
        parts = []
        used = 0
        for ch in (disk.get("children") or []):
            if ch.get("type") not in ("part", "lvm", "crypt"): continue
            size = ch.get("size", 0)
            parts.append((used, size, ch.get("name", ""), ch.get("fstype", ""), ch.get("label", "")))
            used += size
        if used < disk_size:
            parts.append((used, disk_size - used, "", "free", "free"))
        self.disk_map.set_disk(disk_size, parts)

    def _update_btns(self):
        has_disk = self.selected_disk is not None
        has_part = self.selected_part is not None
        mounted = (self.selected_part or {}).get("mountpoint")

        self.btn_table.setEnabled(has_disk)
        self.btn_create.setEnabled(has_disk)
        self.btn_delete.setEnabled(has_part)
        self.btn_format.setEnabled(has_part and not mounted)
        self.btn_mount.setEnabled(has_part and not mounted)
        self.btn_unmount.setEnabled(has_part and bool(mounted))

    def _do_new_table(self):
        if not self.selected_disk: return
        dev = self.selected_disk["name"]
        dlg = CreateTableDialog(dev, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._run_op("create_table", dlg.result_data)

    def _do_create(self):
        if not self.selected_disk: return
        disk = self.selected_disk
        dev = disk["name"]
        disk_size_mb = disk.get("size", 0) // (1024 * 1024)

        used_mb = sum(ch.get("size", 0) for ch in (disk.get("children") or [])
                      if ch.get("type") in ("part", "lvm", "crypt")) // (1024 * 1024)
        free_start = used_mb + 1  # 1 MiB after last partition
        free_end = disk_size_mb

        if free_end - free_start < 1:
            QMessageBox.warning(self, "No Space", "No free space available on this disk!")
            return

        dlg = CreatePartDialog(dev, free_start, free_end, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._run_op("create_part", dlg.result_data)

    def _do_delete(self):
        if not self.selected_part or not self.selected_disk: return
        part_name = self.selected_part["name"]
        disk_name = self.selected_disk["name"]

        num = part_name.replace(disk_name, "").lstrip("p")
        try:
            num = int(num)
        except ValueError:
            QMessageBox.warning(self, "Error", f"Cannot determine partition number for {part_name}")
            return

        r = QMessageBox.warning(self, "Delete Partition",
            f"Delete <b>{part_name}</b>?<br><br>"
            f"This will destroy all data on this partition!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self._run_op("delete_part", {"device": disk_name, "number": num})

    def _do_format(self):
        if not self.selected_part: return
        dlg = FormatDialog(self.selected_part["name"], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._run_op("format_part", dlg.result_data)

    def _do_mount(self):
        if not self.selected_part: return
        dlg = MountDialog(self.selected_part["name"], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._run_op("mount", dlg.result_data)

    def _do_unmount(self):
        if not self.selected_part: return
        part = self.selected_part["name"]
        r = QMessageBox.question(self, "Unmount",
            f"Unmount <b>{part}</b>?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self._run_op("unmount", {"partition": part})

    def _run_op(self, op, args):
        self.worker = PartitionWorker(op, args)
        self.worker.log_msg.connect(self._log)
        self.worker.finished_sig.connect(self._on_done)
        self._set_running(True)
        self.worker.start()

    def _set_running(self, on):
        for b in [self.btn_table, self.btn_create, self.btn_delete,
                  self.btn_format, self.btn_mount, self.btn_unmount,
                  self.refresh_btn, self.tree]:
            b.setEnabled(not on)
        if on:
            self.status.setText("Working...")
            self.status.setStyleSheet("color: #ffa500;")
        else:
            self.status.setText("Ready")
            self.status.setStyleSheet("color: #00d4aa;")

    def _on_done(self, ok, msg):
        self._set_running(False)
        if ok:
            self._log(f"OK: {msg}")
            QMessageBox.information(self, "Done", msg)
        else:
            self._log(f"Error: {msg}")
            QMessageBox.critical(self, "Error", msg)
        QTimer.singleShot(300, self._refresh)

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.setFixedHeight(120)
            self.log_box.setMaximumHeight(120)
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
        print("SystemPart requires root. Run: sudo python3 systempart.py")
        try:
            a = QApplication(sys.argv); a.setStyleSheet(STYLESHEET)
            QMessageBox.warning(None, "Root Required",
                "Run with:\n  sudo python3 systempart.py")
        except: pass
        sys.exit(1)

    a = QApplication(sys.argv)
    a.setApplicationName("SystemPart")
    a.setStyleSheet(STYLESHEET)
    w = SystemPartWindow()
    w.show()
    sys.exit(a.exec())

if __name__ == "__main__":
    main()
