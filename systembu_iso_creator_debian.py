#!/usr/bin/env python3
"""
Standalone Debian CLI builder for the SystemBU live ISO.

This script is intentionally independent from systembu_iso_creator_wsl.pyw and
contains its own build logic for use directly inside a Debian shell.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR / "tools"

DEFAULT_BUILD_DIR = Path("/var/tmp/systembu_liveiso_build")
DEFAULT_SUITE = "trixie"
DEFAULT_IMAGE_NAME = "systembu"
DEFAULT_ISO_VOLUME = "SYSTEMBU64"
DEFAULT_MIRROR = "https://deb.debian.org/debian/"
DEFAULT_SQUASHFS_COMPRESSION_TYPE = "xz"
DEFAULT_INCLUDE_NONFREE_FIRMWARE = True
LOCAL_PACKAGE_VERSION = "1.0.0"

FULL_BUILD_CLEAN_ARGS = ("--purge",)

QUIET_BOOT_ARGS = (
    "quiet splash loglevel=3 systemd.show_status=false "
    "udev.log_priority=3 vt.global_cursor_default=0"
)

REQUIRED_HOST_PACKAGES = [
    "live-build", "debootstrap", "squashfs-tools", "xorriso",
    "grub-pc-bin", "grub-efi-amd64-bin", "mtools", "dosfstools",
]

SYSTEMBU_PACKAGE_LIST = [
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


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def is_root_user() -> bool:
    return getattr(os, "geteuid", lambda: -1)() == 0


def resolve_tool_path(filename: str) -> Path:
    preferred = TOOLS_DIR / filename
    if preferred.is_file():
        return preferred
    return SCRIPT_DIR / filename


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
    include_nonfree_firmware: bool = DEFAULT_INCLUDE_NONFREE_FIRMWARE

    @property
    def workspace(self) -> Path:
        return self.build_dir

    @property
    def final_iso_name(self) -> str:
        return f"{self.image_name}-amd64.iso"

    @property
    def final_iso_path(self) -> Path:
        return SCRIPT_DIR / self.final_iso_name


class UserCancelledError(RuntimeError):
    pass


class DebianIsoBuilder:
    BUILD_ENV = {
        "DEBIAN_FRONTEND": "noninteractive",
        "DEBCONF_NOWARNINGS": "yes",
        "APT_LISTCHANGES_FRONTEND": "none",
        "LANG": "C",
        "LC_ALL": "C",
        "LB_DEBCONF_FRONTEND": "noninteractive",
        "TMPDIR": "/var/tmp",
    }

    def __init__(self, cfg: BuildConfig):
        self.cfg = cfg
        self._stop = False
        self._proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self._stop = True
        if (proc := self._proc) and proc.poll() is None:
            self.log("Stop requested...")
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                proc.terminate()

    def log(self, text: str) -> None:
        print(text, flush=True)

    def status(self, text: str) -> None:
        print(f"[STATUS] {text}", flush=True)

    def build(self) -> Path:
        cleanup = False
        started = time.monotonic()
        try:
            self._validate()
            self._ensure_workspace()
            cleanup = True
            if self.cfg.install_prereqs:
                self.status("Host dependencies")
                self._ensure_host_prereqs()
            self.status("Preparing workspace")
            self._run(["lb", "clean", *FULL_BUILD_CLEAN_ARGS], cwd=self.cfg.workspace, allow_fail=True)
            self.status("Configuring live-build")
            self._run_lb_config()
            self.status("Writing customizations")
            self._write_customizations()
            self.status("Building ISO")
            self._run(["lb", "build"], cwd=self.cfg.workspace)
            iso_path = self._find_and_move_iso()
            cleanup = False
            self._cleanup()
            elapsed = int(round(time.monotonic() - started))
            self.log(f"ISO created: {iso_path}")
            self.log(f"Elapsed: {elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}")
            return iso_path
        except UserCancelledError as exc:
            raise RuntimeError("Build aborted.") from exc
        finally:
            if cleanup:
                self._cleanup()

    def _validate(self) -> None:
        if os.name == "nt":
            raise RuntimeError("This builder must run in Debian/Linux, not Windows.")
        if not is_root_user():
            raise RuntimeError("Please run as root, e.g. sudo -E python3 systembu_iso_creator_debian.py")
        if not shutil.which("dpkg-deb"):
            raise RuntimeError("dpkg-deb not found. Please install dpkg.")
        for path, label in (
            (self.cfg.setup_script, "build/systembu_desktop_setup.sh"),
            (self.cfg.systembu_tool, "systembu.py"),
            (self.cfg.systempart_tool, "systempart.py"),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"{label} not found: {path}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", self.cfg.image_name):
            raise ValueError("Invalid image name. Use lowercase letters, numbers, dot, dash or underscore.")
        if not self.cfg.mirror.startswith(("http://", "https://")):
            raise ValueError("Mirror must start with http:// or https://")

    def _ensure_host_prereqs(self) -> None:
        missing = self._missing_host_packages(REQUIRED_HOST_PACKAGES)
        if not missing:
            self.log("Host dependencies already installed.")
            return
        self.status("Installing missing host dependencies")
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
            self.log(f"Recreating workspace: {ws}")
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        self.log(f"Workspace: {ws}")

    def _run_lb_config(self) -> None:
        nonfree = self.cfg.include_nonfree_firmware
        archive_areas = "main contrib non-free non-free-firmware" if nonfree else "main contrib"
        firmware_enabled = "true" if nonfree else "false"
        cmd = [
            "lb", "config", "noauto",
            "--mode", "debian",
            "--distribution", self.cfg.suite,
            "--architectures", "amd64",
            "--binary-images", "iso-hybrid",
            "--debian-installer", "none",
            "--archive-areas", archive_areas,
            "--firmware-binary", firmware_enabled,
            "--firmware-chroot", firmware_enabled,
            "--mirror-bootstrap", self.cfg.mirror,
            "--mirror-binary", self.cfg.mirror,
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
            raise RuntimeError("lb config failed: config/ is missing.")

        pkg_dir = cfg_dir / "package-lists"
        deb_dir = cfg_dir / "includes.chroot/usr/local/share/systembu/packages"
        inc_dir = cfg_dir / "includes.chroot/root/systembu-build"
        hook_dir = cfg_dir / "hooks/live"
        for directory in (pkg_dir, deb_dir, inc_dir, hook_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self._write_bootloader_overrides(cfg_dir)

        (pkg_dir / "systembu.list.chroot").write_text(
            "\n".join(sorted(set(SYSTEMBU_PACKAGE_LIST))) + "\n",
            encoding="utf-8",
        )

        setup_target = inc_dir / "systembu_desktop_setup.sh"
        setup_target.write_text(self.cfg.setup_script.read_text(errors="replace"), encoding="utf-8")
        os.chmod(setup_target, 0o755)

        package_output_dir = self.cfg.workspace / ".local-debs"
        package_output_dir.mkdir(parents=True, exist_ok=True)
        for deb in self._build_local_packages(package_output_dir):
            shutil.copy2(deb, deb_dir / deb.name)

        hook = hook_dir / "010-systembu-setup.hook.chroot"
        hook.write_text(textwrap.dedent("""\
            #!/bin/sh
            set -e
            export DEBIAN_FRONTEND=noninteractive
            apt-get update
            if ! dpkg -i /usr/local/share/systembu/packages/*.deb; then
              apt-get install -f -y
            fi
            chmod +x /root/systembu-build/systembu_desktop_setup.sh
            /root/systembu-build/systembu_desktop_setup.sh
        """), encoding="utf-8")
        os.chmod(hook, 0o755)

    def _tool_packages(self) -> list[ToolPackage]:
        return [
            ToolPackage(
                package_name="systembu",
                display_name="SystemBU",
                summary="SystemBU utility for the live system",
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
                summary="SystemPart utility for the live system",
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
        built_packages: list[Path] = []

        for spec in self._tool_packages():
            pkg_root = build_root / spec.package_name
            if pkg_root.exists():
                shutil.rmtree(pkg_root)

            debian_dir = pkg_root / "DEBIAN"
            script_dir = pkg_root / "opt/systembu"
            launcher_dir = pkg_root / "usr/local/bin"
            desktop_dir = pkg_root / "usr/share/applications"
            for directory in (debian_dir, script_dir, launcher_dir, desktop_dir):
                directory.mkdir(parents=True, exist_ok=True)

            (debian_dir / "control").write_text(self._render_control(spec), encoding="utf-8")

            script_target = script_dir / spec.source.name
            script_target.write_bytes(spec.source.read_bytes())
            os.chmod(script_target, 0o755)

            launcher_target = launcher_dir / spec.launcher_name
            launcher_target.write_text(self._render_wrapper(spec), encoding="utf-8")
            os.chmod(launcher_target, 0o755)

            desktop_target = desktop_dir / f"{spec.desktop_id}.desktop"
            desktop_target.write_text(self._render_desktop_entry(spec), encoding="utf-8")
            os.chmod(desktop_target, 0o755)

            deb_path = output_dir / spec.deb_filename
            if deb_path.exists():
                deb_path.unlink()
            self._run(["dpkg-deb", "--build", str(pkg_root), str(deb_path)])
            built_packages.append(deb_path)

        return built_packages

    @staticmethod
    def _render_control(spec: ToolPackage) -> str:
        return textwrap.dedent(f"""\
            Package: {spec.package_name}
            Version: {spec.version}
            Section: utils
            Priority: optional
            Architecture: all
            Maintainer: SystemBU Builder <local@systembu.local>
            Depends: {", ".join(spec.depends)}
            Description: {spec.summary}
             {spec.description}
        """)

    @staticmethod
    def _render_wrapper(spec: ToolPackage) -> str:
        return textwrap.dedent(f"""\
            #!/bin/sh
            set -eu
            script="/opt/systembu/{spec.source.name}"
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
        isolinux_dir = cfg_dir / "bootloaders/isolinux"
        syslinux_common_dir = cfg_dir / "bootloaders/syslinux_common"
        grub_dir = cfg_dir / "bootloaders/grub-pc"
        for directory in (isolinux_dir, syslinux_common_dir, grub_dir):
            directory.mkdir(parents=True, exist_ok=True)

        (isolinux_dir / "isolinux.cfg").write_text(textwrap.dedent("""\
            include menu.cfg
            default live-amd64
            prompt 0
            timeout 1
        """), encoding="utf-8")

        (syslinux_common_dir / "live.cfg.in").write_text(textwrap.dedent("""\
            label live-@FLAVOUR@
            	menu label ^Live system (@FLAVOUR@)
            	menu default
            	linux @LINUX@
            	initrd @INITRD@
            	append @APPEND_LIVE@
        """), encoding="utf-8")

        grub_template = Path("/usr/share/live/build/bootloaders/grub-pc/config.cfg")
        grub_cfg = grub_template.read_text(errors="replace") if grub_template.is_file() else "set default=0\n"

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
        (grub_dir / "config.cfg").write_text(grub_cfg, encoding="utf-8")

    def _find_and_move_iso(self) -> Path:
        isos = sorted(self.cfg.workspace.glob("*.iso"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not isos:
            raise RuntimeError("No ISO file found in workspace.")
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
            self.log(f"Removing temp directory: {ws}")
            shutil.rmtree(ws, ignore_errors=True)

    def _run(self, cmd: list[str], *, cwd: Path | None = None, allow_fail: bool = False) -> None:
        if self._stop:
            raise UserCancelledError()

        self.log(f"$ {shell_join(cmd)}")
        env = {**os.environ, **self.BUILD_ENV}
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        self._proc = proc
        try:
            for line in proc.stdout or []:
                self.log(line.rstrip("\n"))
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
            raise RuntimeError(f"Command failed (exit {rc}): {shell_join(cmd)}")


def make_default_config(*, build_dir: Path | None = None) -> BuildConfig:
    return BuildConfig(
        build_dir=build_dir or DEFAULT_BUILD_DIR,
        image_name=DEFAULT_IMAGE_NAME,
        suite=DEFAULT_SUITE,
        mirror=DEFAULT_MIRROR,
        setup_script=SCRIPT_DIR / "build" / "systembu_desktop_setup.sh",
        systembu_tool=resolve_tool_path("systembu.py"),
        systempart_tool=resolve_tool_path("systempart.py"),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the SystemBU Debian live ISO directly in a Debian shell.",
    )
    parser.add_argument(
        "--build-dir",
        help="Optional build directory inside the Linux filesystem.",
    )
    parser.add_argument(
        "--suite",
        default=DEFAULT_SUITE,
        help=f"Debian suite to build. Default: {DEFAULT_SUITE}",
    )
    parser.add_argument(
        "--mirror",
        default=DEFAULT_MIRROR,
        help=f"Debian mirror URL. Default: {DEFAULT_MIRROR}",
    )
    parser.add_argument(
        "--image-name",
        default=DEFAULT_IMAGE_NAME,
        help=f"Base image name before the -amd64.iso suffix. Default: {DEFAULT_IMAGE_NAME}",
    )
    parser.add_argument(
        "--skip-prereqs",
        action="store_true",
        help="Do not auto-install host dependencies.",
    )
    parser.add_argument(
        "--without-nonfree-firmware",
        action="store_true",
        help="Disable contrib/non-free firmware packages in the build.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = make_default_config(
        build_dir=Path(args.build_dir) if args.build_dir else None,
    )
    cfg.suite = args.suite
    cfg.mirror = args.mirror
    cfg.image_name = args.image_name
    cfg.install_prereqs = not args.skip_prereqs
    cfg.include_nonfree_firmware = not args.without_nonfree_firmware

    print(f"Output ISO: {cfg.final_iso_path}", flush=True)
    builder = DebianIsoBuilder(cfg)
    try:
        builder.build()
    except KeyboardInterrupt:
        builder.stop()
        print("Build aborted.", file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
