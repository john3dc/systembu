#!/usr/bin/env python3
"""
tkinter GUI for building a Debian amd64 Live ISO in WSL using live-build.

Reuses the local setup script and packages the Python SystemBU and SystemPart
tools as local Debian packages that are baked into the ISO and installed in the
live system.

- If started with Windows-Python, relaunches itself in WSL (as root) via WSLg.
- Build logic requires WSL/Linux root (apt-get, dpkg-deb, lb build).
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR / "tools"
DEFAULT_BUILD_DIR = Path("/var/tmp/rescue_system_liveiso_build")
DEFAULT_SUITE = "trixie"
DEFAULT_IMAGE_NAME = "systembu"
DEFAULT_ISO_VOLUME = "RESCUE_TRIXIE64"
DEFAULT_MIRROR = "https://deb.debian.org/debian/"
DEFAULT_WSL_DISTRO = os.environ.get("RESCUE_WSL_DISTRO", "Debian")
HEADLESS_WSL_WORKER_ARG = "--headless-wsl-worker"
HEADLESS_ISO_MARKER = "__RESCUE_ISO__ "
HEADLESS_ERR_MARKER = "__RESCUE_ERROR__ "
FULL_BUILD_CLEAN_ARGS = ("--purge",)
DEFAULT_SQUASHFS_COMPRESSION_TYPE = "xz"

REQUIRED_HOST_PACKAGES = [
    "live-build", "debootstrap", "squashfs-tools", "xorriso",
    "grub-pc-bin", "grub-efi-amd64-bin", "mtools", "dosfstools",
]

RESCUE_PACKAGE_LIST = [
    "live-boot", "live-config", "systemd-sysv", "sudo", "dbus-x11",
    "network-manager", "network-manager-gnome", "ca-certificates",
    "linux-image-amd64",
    "xauth", "xinit", "xkb-data", "x11-xkb-utils", "x11-xserver-utils",
    "xserver-xorg-core", "xserver-xorg-input-all", "xserver-xorg-video-all",
    "xserver-xorg-legacy",
    "xfce4-appfinder", "xfce4-panel", "xfce4-session",
    "xfce4-settings", "xfce4-screenshooter", "xfce4-terminal",
    "xfdesktop4", "xfwm4", "thunar", "mousepad",
    "lightdm", "lightdm-gtk-greeter",
    "adwaita-icon-theme", "fonts-cantarell", "fonts-dejavu-core",
    "python3", "python3-pyside6.qtcore", "python3-pyside6.qtgui", "python3-pyside6.qtwidgets",
    "python3-cryptography", "pv", "gzip", "xz-utils", "util-linux",
    "parted", "partclone", "e2fsprogs", "ntfs-3g", "dosfstools",
    "plymouth", "plymouth-label", "plymouth-themes",
]

QUIET_BOOT_ARGS = (
    "quiet splash loglevel=3 systemd.show_status=false "
    "udev.log_priority=3 vt.global_cursor_default=0"
)
LOCAL_PACKAGE_VERSION = "1.0.0"


def _resolve_tool_path(filename: str) -> Path:
    """Prefer tools/<filename>, fall back to legacy top-level file."""
    preferred = TOOLS_DIR / filename
    if preferred.is_file():
        return preferred
    return SCRIPT_DIR / filename


def _make_default_config(
    *,
    build_dir: Path | None = None,
    install_prereqs: bool = True,
    wsl_distro: str | None = None,
) -> "BuildConfig":
    return BuildConfig(
        build_dir=build_dir or DEFAULT_BUILD_DIR,
        image_name=DEFAULT_IMAGE_NAME,
        suite=DEFAULT_SUITE,
        mirror=DEFAULT_MIRROR,
        setup_script=SCRIPT_DIR / "build" / "systembu_desktop_setup.sh",
        systembu_tool=_resolve_tool_path("systembu.py"),
        systempart_tool=_resolve_tool_path("systempart.py"),
        install_prereqs=install_prereqs,
        wsl_distro=wsl_distro or DEFAULT_WSL_DISTRO,
    )


def _make_config_from_args(args: argparse.Namespace) -> "BuildConfig":
    build_dir = Path(args.build_dir) if args.build_dir else None
    return _make_default_config(
        build_dir=build_dir,
        install_prereqs=not args.skip_prereqs,
        wsl_distro=args.wsl_distro,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Buildet die SystemBU-Debian-Live-ISO lokal oder ueber WSL.",
    )
    parser.add_argument(HEADLESS_WSL_WORKER_ARG, dest="headless_wsl_worker", action="store_true")
    parser.add_argument("--cli", action="store_true", help="Headless Build ohne GUI starten.")
    parser.add_argument(
        "--wsl-distro",
        default=DEFAULT_WSL_DISTRO,
        help="Bevorzugte WSL-Distro fuer Windows-Relay.",
    )
    parser.add_argument(
        "--build-dir",
        help="Optionales Build-Verzeichnis im Linux-Dateisystem.",
    )
    parser.add_argument(
        "--skip-prereqs",
        action="store_true",
        help="Host-Abhaengigkeiten nicht automatisch installieren.",
    )
    return parser.parse_args(argv)


def _decode_wsl_output(raw: bytes) -> str:
    for enc in ("utf-8", "utf-16-le", "cp1252"):
        try:
            text = raw.decode(enc)
            if enc == "utf-8" and text.count("\x00") > max(2, len(text) // 8):
                continue
            return text
        except Exception:
            continue
    return raw.decode(errors="ignore")


def _subprocess_hidden_kwargs() -> dict:
    if os.name != "nt":
        return {}
    kwargs: dict = {}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        si.wShowWindow = 0
        kwargs["startupinfo"] = si
    except Exception:
        pass
    no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if no_window:
        kwargs["creationflags"] = no_window
    return kwargs


def _list_wsl_distros() -> list[str]:
    try:
        raw = subprocess.check_output(
            ["wsl.exe", "-l", "-q"],
            stderr=subprocess.STDOUT,
            **_subprocess_hidden_kwargs(),
        )
    except Exception:
        return []
    out = _decode_wsl_output(raw)
    return [d for line in out.splitlines() if (d := line.strip().replace("\x00", ""))]


def _pick_wsl_distro(preferred: str | None = None) -> str | None:
    distros = _list_wsl_distros()
    if not distros:
        return None
    preferred_names = [n for n in (preferred, DEFAULT_WSL_DISTRO) if n]
    for preferred_name in preferred_names:
        for name in distros:
            if name.lower() == preferred_name.lower():
                return name
        for name in distros:
            if name.lower().startswith(preferred_name.lower()):
                return name
    for name in distros:
        if name.lower().startswith("debian"):
            return name
    return distros[0] if len(distros) == 1 else None


def _win_to_wsl_path(path: str, wsl_base_cmd: list[str]) -> str:
    norm = path.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", norm)
    if m:
        return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    raw = subprocess.check_output(
        [*wsl_base_cmd, "wslpath", "-a", path],
        stderr=subprocess.STDOUT,
        **_subprocess_hidden_kwargs(),
    )
    return _decode_wsl_output(raw).strip().replace("\x00", "")


def is_wsl() -> bool:
    for p in ("/proc/sys/kernel/osrelease", "/proc/version"):
        try:
            if any(k in Path(p).read_text(errors="ignore").lower() for k in ("microsoft", "wsl")):
                return True
        except OSError:
            pass
    return False


def is_root_user() -> bool:
    return getattr(os, "geteuid", lambda: -1)() == 0


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(p) for p in cmd)


def _launch_in_wsl_from_windows() -> int:
    distro = _pick_wsl_distro()
    if not distro:
        print("Keine WSL-Distribution gefunden. Bitte Debian in WSL installieren.", file=sys.stderr)
        return 1
    wsl_base = ["wsl.exe", "-d", distro]
    try:
        script_wsl = _win_to_wsl_path(str(Path(__file__).resolve()), wsl_base)
        cwd_wsl = _win_to_wsl_path(str(Path.cwd()), wsl_base)
    except Exception as exc:
        print(f"WSL-Pfade nicht ermittelbar: {exc}", file=sys.stderr)
        return 1

    shell_cmd = textwrap.dedent(f"""\
        set -e; export DEBIAN_FRONTEND=noninteractive
        cd {shlex.quote(cwd_wsl)}
        if [ -d /mnt/wslg ]; then
          [ -S /mnt/wslg/runtime-dir/wayland-0 ] && {{
            export XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir
            export WAYLAND_DISPLAY=wayland-0
            export DISPLAY="${{DISPLAY:-:0}}"
            export PULSE_SERVER="${{PULSE_SERVER:-/mnt/wslg/PulseServer}}"
          }}
          [ -z "${{DISPLAY:-}}" ] && [ -S /tmp/.X11-unix/X0 ] && export DISPLAY=:0
        fi
        command -v python3 >/dev/null || {{ apt-get update && apt-get install -y python3; }}
        python3 -c 'import tkinter' 2>/dev/null || {{ apt-get update && apt-get install -y python3-tk; }}
        [ -S /mnt/wslg/runtime-dir/wayland-0 ] || [ -S /tmp/.X11-unix/X0 ] || {{ echo "Kein Display." >&2; exit 2; }}
        exec python3 {shlex.quote(script_wsl)}""").strip()

    print(f"Starte GUI in WSL (Distro: {distro}) ...")
    try:
        r = subprocess.run(
            [*wsl_base, "-u", "root", "bash", "-lc", shell_cmd],
            **_subprocess_hidden_kwargs(),
        )
        return r.returncode
    except FileNotFoundError:
        print("wsl.exe nicht gefunden.", file=sys.stderr)
        return 1


@dataclass
class BuildConfig:
    build_dir: Path
    image_name: str
    suite: str
    mirror: str
    setup_script: Path
    systembu_tool: Path
    systempart_tool: Path
    install_prereqs: bool = True
    wsl_distro: str | None = None
    include_nonfree_firmware: bool = True

    @property
    def workspace(self) -> Path:
        return self.build_dir

    @property
    def final_iso_name(self) -> str:
        return f"{self.image_name}-amd64.iso"

    @property
    def final_iso_path(self) -> Path:
        return self.setup_script.parent / self.final_iso_name


@dataclass(frozen=True)
class ToolPackage:
    package_name: str
    display_name: str
    summary: str
    description: str
    source: Path
    launcher_name: str
    desktop_id: str
    desktop_comment: str
    icon_name: str
    depends: tuple[str, ...]
    version: str = LOCAL_PACKAGE_VERSION

    @property
    def deb_filename(self) -> str:
        return f"{self.package_name}_{self.version}_all.deb"


class UserCancelledError(RuntimeError):
    pass


StrCallback = Callable[[str], None]


def _noop_callback(_: str) -> None:
    return


class BuildWorker:
    _BUILD_ENV = {
        "DEBIAN_FRONTEND": "noninteractive",
        "DEBCONF_NOWARNINGS": "yes",
        "APT_LISTCHANGES_FRONTEND": "none",
        "LANG": "C", "LC_ALL": "C",
        "LB_DEBCONF_FRONTEND": "noninteractive",
        "TMPDIR": "/var/tmp",
    }

    def __init__(
        self,
        cfg: BuildConfig,
        on_log: StrCallback | None = None,
        on_status: StrCallback | None = None,
        on_finished: StrCallback | None = None,
        on_failed: StrCallback | None = None,
    ):
        self.cfg = cfg
        self.on_log = on_log or _noop_callback
        self.on_status = on_status or _noop_callback
        self.on_finished = on_finished or _noop_callback
        self.on_failed = on_failed or _noop_callback
        self._stop = False
        self._proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self._stop = True
        if (p := self._proc) and p.poll() is None:
            self.on_log("Stop angefordert â€¦")
            try:
                p.send_signal(signal.SIGINT)
            except Exception:
                p.terminate()

    def run(self) -> None:
        cleanup = False
        try:
            self._validate()
            self._ensure_workspace()
            cleanup = True
            if self.cfg.install_prereqs:
                self.on_status("Host-AbhÃ¤ngigkeiten installieren")
                self._ensure_host_prereqs()
            self.on_status("Workspace vorbereiten")
            self._run(["lb", "clean", *FULL_BUILD_CLEAN_ARGS], cwd=self.cfg.workspace, allow_fail=True)
            self.on_status("live-build konfigurieren")
            self._run_lb_config()
            self.on_status("Chroot-Dateien und Hook vorbereiten")
            self._write_customizations()
            self.on_status("ISO bauen (dauert â€¦)")
            self._run(["lb", "build"], cwd=self.cfg.workspace)
            iso = self._find_and_move_iso()
            cleanup = False
            self._cleanup()
            self.on_finished(str(iso))
        except UserCancelledError:
            if cleanup:
                self._cleanup()
            self.on_failed("Build abgebrochen.")
        except Exception as exc:
            if cleanup:
                self._cleanup()
            self.on_failed(str(exc))

    def _validate(self) -> None:
        if os.name == "nt":
            raise RuntimeError("Muss in WSL/Linux laufen, nicht mit Windows-Python.")
        if not is_root_user():
            raise RuntimeError("Bitte als root starten (sudo -E python3 â€¦).")
        if not shutil.which("dpkg-deb"):
            raise RuntimeError("dpkg-deb nicht gefunden. Bitte dpkg installieren.")
        for fp, label in ((self.cfg.setup_script, "build/systembu_desktop_setup.sh"),
                          (self.cfg.systembu_tool, "systembu.py"),
                          (self.cfg.systempart_tool, "systempart.py")):
            if not fp.is_file():
                raise FileNotFoundError(f"{label} nicht gefunden: {fp}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", self.cfg.image_name):
            raise ValueError("UngÃ¼ltiger ISO-Name (nur Kleinbuchstaben, Zahlen, .-_ erlaubt).")
        if not self.cfg.mirror.startswith(("http://", "https://")):
            raise ValueError("Mirror muss mit http(s):// beginnen.")

    def _ensure_host_prereqs(self) -> None:
        missing = self._missing_host_packages(REQUIRED_HOST_PACKAGES)
        if not missing:
            self.on_log("Host-Abhaengigkeiten bereits vorhanden.")
            return
        self.on_status("Fehlende Host-Abhaengigkeiten installieren")
        self._run(["apt-get", "update"])
        self._run(["apt-get", "install", "-y", *missing])

    @staticmethod
    def _missing_host_packages(packages: list[str]) -> list[str]:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}\t${Status}\n", *packages],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        installed: set[str] = set()
        for line in proc.stdout.splitlines():
            package, sep, status = line.partition("\t")
            if sep and status.strip() == "install ok installed":
                installed.add(package.strip())
        return [pkg for pkg in packages if pkg not in installed]

    def _ensure_workspace(self) -> None:
        ws = self.cfg.workspace
        if ws.exists():
            self.on_log(f"Workspace wird neu erstellt: {ws}")
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        if str(ws).startswith("/mnt/"):
            self.on_log("Hinweis: Build im Windows-FS (/mnt/â€¦) â€“ kann langsamer sein.")
        self.on_log(f"Workspace: {ws}")

    def _run_lb_config(self) -> None:
        nonfree = self.cfg.include_nonfree_firmware
        areas = "main contrib non-free non-free-firmware" if nonfree else "main contrib"
        fw = "true" if nonfree else "false"
        cmd = [
            "lb", "config", "noauto",
            "--mode", "debian", "--distribution", self.cfg.suite,
            "--architectures", "amd64", "--binary-images", "iso-hybrid",
            "--debian-installer", "none",
            "--archive-areas", areas,
            "--firmware-binary", fw, "--firmware-chroot", fw,
            "--mirror-bootstrap", self.cfg.mirror, "--mirror-binary", self.cfg.mirror,
            "--image-name", self.cfg.image_name,
            "--iso-volume", DEFAULT_ISO_VOLUME,
            "--bootappend-live",
            (
                "boot=live components locales=en_US.UTF-8 "
                f"keyboard-layouts=de username=diskadmin {QUIET_BOOT_ARGS}"
            ),
            "--bootappend-live-failsafe", "none",
            "--cache", "false",
            "--cache-packages", "false",
            "--cache-indices", "false",
            "--chroot-squashfs-compression-type", DEFAULT_SQUASHFS_COMPRESSION_TYPE,
        ]
        self._run(cmd, cwd=self.cfg.workspace)

    def _write_customizations(self) -> None:
        cfg_dir = self.cfg.workspace / "config"
        if not cfg_dir.exists():
            raise RuntimeError("lb config fehlgeschlagen â€“ config/ fehlt.")
        pkg_dir = cfg_dir / "package-lists"
        deb_dir = cfg_dir / "includes.chroot/usr/local/share/rescue/packages"
        inc_dir = cfg_dir / "includes.chroot/root/rescue-build"
        hook_dir = cfg_dir / "hooks/live"
        for d in (pkg_dir, deb_dir, inc_dir, hook_dir):
            d.mkdir(parents=True, exist_ok=True)

        self._write_bootloader_overrides(cfg_dir)

        (pkg_dir / "rescue.list.chroot").write_text(
            "\n".join(sorted(set(RESCUE_PACKAGE_LIST))) + "\n")

        target = inc_dir / "systembu_desktop_setup.sh"
        target.write_text(self.cfg.setup_script.read_text(errors="replace"))
        os.chmod(target, 0o755)

        package_output_dir = self.cfg.workspace / ".local-debs"
        package_output_dir.mkdir(parents=True, exist_ok=True)
        for deb in self._build_local_packages(package_output_dir):
            shutil.copy2(deb, deb_dir / deb.name)

        hook = hook_dir / "010-rescue-setup.hook.chroot"
        hook.write_text(textwrap.dedent("""\
            #!/bin/sh
            set -e
            export DEBIAN_FRONTEND=noninteractive
            apt-get update
            if ! dpkg -i /usr/local/share/rescue/packages/*.deb; then
              apt-get install -f -y
            fi
            chmod +x /root/rescue-build/systembu_desktop_setup.sh
            /root/rescue-build/systembu_desktop_setup.sh
        """))
        os.chmod(hook, 0o755)

    def _tool_packages(self) -> list[ToolPackage]:
        return [
            ToolPackage(
                package_name="systembu",
                display_name="SystemBU",
                summary="SystemBU utility for the rescue live system",
                description="PySide6-based disk and partition backup interface with partclone support.",
                source=self.cfg.systembu_tool,
                launcher_name="systembu",
                desktop_id="systembu",
                desktop_comment="Disk and partition backup utility",
                icon_name="drive-harddisk",
                depends=(
                    "python3", "python3-pyside6.qtcore", "python3-pyside6.qtgui",
                    "python3-pyside6.qtwidgets", "python3-cryptography", "partclone",
                    "pv", "gzip", "xz-utils", "util-linux", "e2fsprogs",
                    "ntfs-3g", "parted", "sudo",
                ),
            ),
            ToolPackage(
                package_name="systempart",
                display_name="SystemPart",
                summary="SystemPart utility for the rescue live system",
                description="PySide6-based partition manager for inspecting and editing disks.",
                source=self.cfg.systempart_tool,
                launcher_name="systempart",
                desktop_id="systempart",
                desktop_comment="Partition management utility",
                icon_name="drive-multidisk",
                depends=(
                    "python3", "python3-pyside6.qtcore", "python3-pyside6.qtgui",
                    "python3-pyside6.qtwidgets", "parted", "util-linux",
                    "e2fsprogs", "dosfstools", "ntfs-3g", "sudo",
                ),
            ),
        ]

    def _build_local_packages(self, output_dir: Path) -> list[Path]:
        build_root = self.cfg.workspace / ".pkgbuild"
        build_root.mkdir(parents=True, exist_ok=True)
        built: list[Path] = []

        for spec in self._tool_packages():
            pkg_root = build_root / spec.package_name
            if pkg_root.exists():
                shutil.rmtree(pkg_root)

            debian_dir = pkg_root / "DEBIAN"
            script_dir = pkg_root / "opt/rescue-system"
            launcher_dir = pkg_root / "usr/local/bin"
            desktop_dir = pkg_root / "usr/share/applications"
            for d in (debian_dir, script_dir, launcher_dir, desktop_dir):
                d.mkdir(parents=True, exist_ok=True)

            control_file = debian_dir / "control"
            control_file.write_text(self._render_control(spec))

            script_target = script_dir / spec.source.name
            script_target.write_bytes(spec.source.read_bytes())
            os.chmod(script_target, 0o755)

            launcher_target = launcher_dir / spec.launcher_name
            launcher_target.write_text(self._render_wrapper(spec))
            os.chmod(launcher_target, 0o755)

            desktop_target = desktop_dir / f"{spec.desktop_id}.desktop"
            desktop_target.write_text(self._render_desktop_entry(spec))
            os.chmod(desktop_target, 0o755)

            deb_path = output_dir / spec.deb_filename
            if deb_path.exists():
                deb_path.unlink()
            self._run(["dpkg-deb", "--build", str(pkg_root), str(deb_path)])
            built.append(deb_path)

        return built

    @staticmethod
    def _render_control(spec: ToolPackage) -> str:
        return textwrap.dedent(f"""\
            Package: {spec.package_name}
            Version: {spec.version}
            Section: utils
            Priority: optional
            Architecture: all
            Maintainer: Rescue System Builder <local@rescue.system>
            Depends: {", ".join(spec.depends)}
            Description: {spec.summary}
             {spec.description}
        """)

    @staticmethod
    def _render_wrapper(spec: ToolPackage) -> str:
        return textwrap.dedent(f"""\
            #!/bin/sh
            set -eu
            script="/opt/rescue-system/{spec.source.name}"
            if [ "$(id -u)" -ne 0 ]; then
                exec sudo -E python3 "$script" "$@"
            fi
            exec python3 "$script" "$@"
        """)

    @staticmethod
    def _render_desktop_entry(spec: ToolPackage) -> str:
        return textwrap.dedent(f"""\
            [Desktop Entry]
            Type=Application
            Name={spec.display_name}
            Comment={spec.desktop_comment}
            Exec=/usr/local/bin/{spec.launcher_name}
            Icon={spec.icon_name}
            Terminal=false
            Categories=System;Utility;
        """)

    def _write_bootloader_overrides(self, cfg_dir: Path) -> None:
        # Force auto-boot in BIOS/UEFI live media without waiting at boot menu.
        isolinux_dir = cfg_dir / "bootloaders/isolinux"
        syslinux_common_dir = cfg_dir / "bootloaders/syslinux_common"
        grub_dir = cfg_dir / "bootloaders/grub-pc"
        for d in (isolinux_dir, syslinux_common_dir, grub_dir):
            d.mkdir(parents=True, exist_ok=True)

        (isolinux_dir / "isolinux.cfg").write_text(textwrap.dedent("""\
            include menu.cfg
            default live-amd64
            prompt 0
            timeout 1
        """))

        (syslinux_common_dir / "live.cfg.in").write_text(textwrap.dedent("""\
            label live-@FLAVOUR@
            	menu label ^Live system (@FLAVOUR@)
            	menu default
            	linux @LINUX@
            	initrd @INITRD@
            	append @APPEND_LIVE@
        """))

        grub_template = Path("/usr/share/live/build/bootloaders/grub-pc/config.cfg")
        if grub_template.is_file():
            grub_cfg = grub_template.read_text(errors="replace")
        else:
            grub_cfg = "set default=0\n"

        if re.search(r"(?m)^set\s+default=", grub_cfg):
            grub_cfg = re.sub(r"(?m)^set\s+default=.*$", "set default=0", grub_cfg)
        else:
            grub_cfg = "set default=0\n" + grub_cfg

        if re.search(r"(?m)^set\s+timeout_style=", grub_cfg):
            grub_cfg = re.sub(r"(?m)^set\s+timeout_style=.*$", "set timeout_style=hidden", grub_cfg)
        else:
            grub_cfg = "set timeout_style=hidden\n" + grub_cfg

        if re.search(r"(?m)^set\s+timeout=", grub_cfg):
            grub_cfg = re.sub(r"(?m)^set\s+timeout=.*$", "set timeout=0", grub_cfg)
        else:
            grub_cfg = "set timeout=0\n" + grub_cfg

        if not grub_cfg.endswith("\n"):
            grub_cfg += "\n"
        (grub_dir / "config.cfg").write_text(grub_cfg)

    def _find_and_move_iso(self) -> Path:
        isos = sorted(self.cfg.workspace.glob("*.iso"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not isos:
            raise RuntimeError("Keine ISO-Datei im Workspace gefunden.")
        built = isos[0]
        target = self.cfg.final_iso_path
        if built.resolve() != target.resolve():
            if target.exists():
                target.unlink()
            shutil.move(str(built), str(target))
        return target

    def _cleanup(self) -> None:
        ws = self.cfg.workspace
        if ws.exists():
            self.on_log(f"LÃ¶sche Temp-Ordner: {ws}")
            shutil.rmtree(ws, ignore_errors=True)

    def _run(self, cmd: list[str], *, cwd: Path | None = None, allow_fail: bool = False) -> None:
        if self._stop:
            raise UserCancelledError()
        self.on_log(f"$ {shell_join(cmd)}")
        env = {**os.environ, **self._BUILD_ENV}
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env, stdin=subprocess.DEVNULL)
        self._proc = proc
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                self.on_log(line.rstrip("\n"))
                if self._stop:
                    try:
                        proc.send_signal(signal.SIGINT)
                    except Exception:
                        proc.terminate()
            rc = proc.wait()
        finally:
            self._proc = None
        if self._stop:
            raise UserCancelledError()
        if rc != 0 and not allow_fail:
            raise RuntimeError(f"Befehl fehlgeschlagen (Exit {rc}): {shell_join(cmd)}")


class WslRelayWorker:
    def __init__(
        self,
        distro: str,
        script_path: Path,
        cwd_path: Path,
        skip_prereqs: bool = False,
        on_log: StrCallback | None = None,
        on_status: StrCallback | None = None,
        on_finished: StrCallback | None = None,
        on_failed: StrCallback | None = None,
    ):
        self.distro = distro
        self.script_path = script_path
        self.cwd_path = cwd_path
        self.skip_prereqs = skip_prereqs
        self.on_log = on_log or _noop_callback
        self.on_status = on_status or _noop_callback
        self.on_finished = on_finished or _noop_callback
        self.on_failed = on_failed or _noop_callback
        self._proc: subprocess.Popen[str] | None = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        if (p := self._proc) and p.poll() is None:
            self.on_log("Stop angefordert â€¦")
            try:
                p.terminate()
            except Exception:
                pass

    def run(self) -> None:
        wsl_base = ["wsl.exe", "-d", self.distro]
        try:
            script_wsl = _win_to_wsl_path(str(self.script_path), wsl_base)
            cwd_wsl = _win_to_wsl_path(str(self.cwd_path), wsl_base)
        except Exception as exc:
            self.on_failed(f"WSL-Pfadumwandlung fehlgeschlagen: {exc}")
            return

        relay_args = [HEADLESS_WSL_WORKER_ARG]
        if self.skip_prereqs:
            relay_args.append("--skip-prereqs")
        relay_args_str = " ".join(shlex.quote(arg) for arg in relay_args)

        shell_cmd = textwrap.dedent(f"""\
            set -e; export DEBIAN_FRONTEND=noninteractive
            cd {shlex.quote(cwd_wsl)}
            command -v python3 >/dev/null || {{ apt-get update && apt-get install -y python3; }}
            exec python3 {shlex.quote(script_wsl)} {relay_args_str}""").strip()

        cmd = [*wsl_base, "-u", "root", "bash", "-lc", shell_cmd]
        self.on_log(f"$ {shell_join(cmd)}")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, stdin=subprocess.DEVNULL,
            **_subprocess_hidden_kwargs())
        self._proc = proc

        iso_path = err_msg = None
        q: queue.Queue[str | None] = queue.Queue()

        def reader():
            for raw in proc.stdout:  # type: ignore[union-attr]
                q.put(raw)
            q.put(None)

        threading.Thread(target=reader, daemon=True).start()

        try:
            while True:
                try:
                    raw = q.get(timeout=2.0)
                except queue.Empty:
                    if self._stop and proc.poll() is None:
                        proc.terminate()
                    if proc.poll() is not None:
                        continue
                    continue
                if raw is None:
                    break
                line = raw.rstrip("\r\n")
                if line.startswith(HEADLESS_ISO_MARKER):
                    iso_path = line[len(HEADLESS_ISO_MARKER):].strip()
                elif line.startswith(HEADLESS_ERR_MARKER):
                    err_msg = line[len(HEADLESS_ERR_MARKER):].strip()
                else:
                    if line.startswith("[STATUS] "):
                        self.on_status(line[9:])
                    self.on_log(line)
                if self._stop and proc.poll() is None:
                    proc.terminate()
            proc.wait()
        finally:
            self._proc = None

        if self._stop:
            self.on_failed("Build abgebrochen.")
        elif iso_path:
            self.on_finished(iso_path)
        else:
            self.on_failed(err_msg or f"WSL-Build fehlgeschlagen (Exit {proc.returncode}).")


class MainWindow:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext

        self.tk = tk
        self.messagebox = messagebox
        self.scrolledtext = scrolledtext

        self.root = tk.Tk()
        self.root.title("SystemBU ISO Creator")
        self.root.geometry("980x680")
        self.root.minsize(840, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.worker: BuildWorker | WslRelayWorker | None = None
        self.worker_thread: threading.Thread | None = None
        self.ui_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.build_started_at: float | None = None

        self._init_ui()
        self.root.after(100, self._process_ui_queue)

    def _init_ui(self) -> None:
        tk = self.tk
        root = tk.Frame(self.root, padx=10, pady=10)
        root.pack(fill="both", expand=True)

        info = tk.Label(
            root,
            text="Erstellt eine Debian amd64 Live-ISO mit XFCE + SystemBU und SystemPart "
                 "auf Basis von build/systembu_desktop_setup.sh (fÃ¼r WSL unter Windows).",
            justify="left",
            anchor="w",
            wraplength=920,
        )
        info.pack(fill="x")

        config_row = tk.Frame(root)
        config_row.pack(fill="x", pady=(8, 0))

        self.wsl_distro_var = tk.StringVar(value=_pick_wsl_distro(DEFAULT_WSL_DISTRO) or DEFAULT_WSL_DISTRO)

        tk.Label(config_row, text="WSL-Distro").pack(side="left")
        self.wsl_distro_entry = tk.Entry(config_row, textvariable=self.wsl_distro_var, width=20)
        self.wsl_distro_entry.pack(side="left", padx=(6, 0))
        if os.name != "nt":
            self.wsl_distro_entry.configure(state="disabled")

        btn_row = tk.Frame(root)
        btn_row.pack(fill="x", pady=(8, 0))
        self.start_btn = tk.Button(btn_row, text="Build starten", command=self.start_build)
        self.stop_btn = tk.Button(btn_row, text="Build stoppen", command=self.stop_build, state="disabled")
        self.start_btn.pack(side="left")
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Bereit")
        self.status_label = tk.Label(root, textvariable=self.status_var, anchor="w", justify="left", wraplength=920)
        self.status_label.pack(fill="x", pady=(8, 0))

        self.log_view = self.scrolledtext.ScrolledText(
            root,
            wrap="word",
            font=("Courier New", 10),
        )
        self.log_view.pack(fill="both", expand=True, pady=(8, 0))
        self.log_view.configure(state="disabled")

    def _log(self, text: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", text + "\n")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _queue_log(self, text: str) -> None:
        self.ui_queue.put(("log", text))

    def _queue_status(self, text: str) -> None:
        self.ui_queue.put(("status", text))

    def _queue_finished(self, text: str) -> None:
        self.ui_queue.put(("finished", text))

    def _queue_failed(self, text: str) -> None:
        self.ui_queue.put(("failed", text))

    def _process_ui_queue(self) -> None:
        while True:
            try:
                event, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if event == "log":
                self._log(payload)
            elif event == "status":
                self._set_status(payload)
                self._log(f"[STATUS] {payload}")
            elif event == "finished":
                self._on_finished(payload)
            elif event == "failed":
                self._on_failed(payload)
            elif event == "thread_done":
                self._on_thread_done()

        self.root.after(100, self._process_ui_queue)

    def _default_config(self) -> BuildConfig:
        return _make_default_config(
            wsl_distro=self.wsl_distro_var.get().strip() or DEFAULT_WSL_DISTRO,
        )

    def start_build(self) -> None:
        if self.worker:
            self.messagebox.showwarning("LÃ¤uft", "Es lÃ¤uft bereits ein Build.")
            return
        cfg = self._default_config()
        try:
            self._preflight(cfg)
        except Exception as exc:
            self.messagebox.showerror("Fehler", str(exc))
            return

        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.configure(state="disabled")
        self._log(f"Build: {cfg.suite}/amd64, Mirror: {cfg.mirror}")
        self._log(f"Ausgabe: {cfg.final_iso_path}\n")

        if os.name == "nt":
            distro = _pick_wsl_distro(cfg.wsl_distro)
            if not distro:
                self.messagebox.showerror("Fehler", "Keine WSL-Distro gefunden.")
                return
            self._log(f"WSL-Distro: {distro}")
            self.worker = WslRelayWorker(
                distro,
                Path(__file__).resolve(),
                Path.cwd(),
                on_log=self._queue_log,
                on_status=self._queue_status,
                on_finished=self._queue_finished,
                on_failed=self._queue_failed,
            )
        else:
            self.worker = BuildWorker(
                cfg,
                on_log=self._queue_log,
                on_status=self._queue_status,
                on_finished=self._queue_finished,
                on_failed=self._queue_failed,
            )

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._set_status("Build lÃ¤uft â€¦")
        self.build_started_at = time.monotonic()

        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()

    def _preflight(self, cfg: BuildConfig) -> None:
        for p, label in ((cfg.setup_script, "build/systembu_desktop_setup.sh"),
                         (cfg.systembu_tool, "systembu.py"),
                         (cfg.systempart_tool, "systempart.py")):
            if not p.is_file():
                raise FileNotFoundError(f"{label} nicht gefunden: {p}")
        if os.name == "nt":
            if not _pick_wsl_distro(cfg.wsl_distro):
                raise RuntimeError(f"WSL-Distro nicht gefunden: {cfg.wsl_distro}")
        elif not is_root_user():
            raise PermissionError("Bitte mit sudo -E starten.")

    def _run_worker(self) -> None:
        try:
            if self.worker:
                self.worker.run()
        except Exception as exc:
            self._queue_failed(str(exc))
        finally:
            self.ui_queue.put(("thread_done", ""))

    def stop_build(self) -> None:
        if self.worker:
            self._set_status("Stoppe â€¦")
            self.worker.stop()
            self.stop_btn.configure(state="disabled")

    def _on_finished(self, iso: str) -> None:
        elapsed = ""
        if self.build_started_at is not None:
            elapsed_seconds = max(0.0, time.monotonic() - self.build_started_at)
            total_seconds = int(round(elapsed_seconds))
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            elapsed = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        self._set_status("Build erfolgreich")
        self._log(f"\nFertig: {iso}")
        if elapsed:
            self.messagebox.showinfo("Fertig", f"ISO erstellt:\n{iso}\n\nBenÃ¶tigte Zeit: {elapsed}")
        else:
            self.messagebox.showinfo("Fertig", f"ISO erstellt:\n{iso}")

    def _on_failed(self, msg: str) -> None:
        self._set_status("Fehlgeschlagen")
        self._log(f"\nFEHLER: {msg}")
        self.messagebox.showerror("Fehler", msg)

    def _on_thread_done(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.build_started_at = None
        self.worker = None
        self.worker_thread = None

    def _on_close(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self.messagebox.showwarning("LÃ¤uft", "Bitte erst den Build stoppen.")
        else:
            self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def _run_build(cfg: BuildConfig, *, emit_markers: bool) -> int:
    result: dict[str, str] = {}
    worker = BuildWorker(
        cfg,
        on_log=lambda l: print(l, flush=True),
        on_status=lambda l: print(f"[STATUS] {l}", flush=True),
        on_finished=lambda p: result.setdefault("iso", p),
        on_failed=lambda m: result.setdefault("error", m),
    )
    try:
        worker.run()
    except Exception as exc:
        result.setdefault("error", str(exc))
    if "iso" in result:
        if emit_markers:
            print(f"{HEADLESS_ISO_MARKER}{result['iso']}", flush=True)
        else:
            print(f"ISO erstellt: {result['iso']}", flush=True)
        return 0
    error = result.get("error", "Unbekannter Fehler")
    if emit_markers:
        print(f"{HEADLESS_ERR_MARKER}{error}", flush=True)
    else:
        print(f"FEHLER: {error}", file=sys.stderr, flush=True)
    return 1


def run_headless_wsl_worker(args: argparse.Namespace) -> int:
    return _run_build(_make_config_from_args(args), emit_markers=True)


def run_cli(args: argparse.Namespace) -> int:
    return _run_build(_make_config_from_args(args), emit_markers=False)


def main(args: argparse.Namespace) -> int:
    if args.cli:
        return run_cli(args)
    try:
        import tkinter  # noqa: F401
    except Exception as exc:
        print(f"tkinter ist nicht verfÃ¼gbar: {exc}", file=sys.stderr)
        return 1
    window = MainWindow()
    return window.run()


if __name__ == "__main__":
    cli_args = _parse_args()
    if cli_args.headless_wsl_worker:
        sys.exit(run_headless_wsl_worker(cli_args))
    sys.exit(main(cli_args))
