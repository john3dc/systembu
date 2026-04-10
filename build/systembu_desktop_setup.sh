#!/bin/bash
set -euo pipefail

REAL_USER="${REAL_USER:-diskadmin}"
REAL_HOME="/home/$REAL_USER"
WALLPAPER_DIR="/usr/local/share/rescue/backgrounds"
WALLPAPER_FILE="$WALLPAPER_DIR/rescue-orbit.svg"
QUIET_BOOT_ARGS="quiet splash loglevel=3 systemd.show_status=false udev.log_priority=3 vt.global_cursor_default=0"
GTK_THEME="Adwaita-dark"
ICON_THEME="Adwaita"
UI_FONT="Cantarell 10"
MONO_FONT="DejaVu Sans Mono 10"

log()  { printf '\033[0;36m[*]\033[0m %s\n' "$1"; }
ok()   { printf '\033[0;32m[+]\033[0m %s\n' "$1"; }
warn() { printf '\033[0;33m[!]\033[0m %s\n' "$1"; }
die()  { printf '\033[0;31m[!]\033[0m %s\n' "$1" >&2; exit 1; }

have() {
    command -v "$1" >/dev/null 2>&1
}

need_root() {
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "Must be run as root."
}

maybe_systemctl() {
    have systemctl || return 0
    systemctl "$@" 2>/dev/null || true
}

write_wallpaper() {
    log "Writing wallpaper..."
    mkdir -p "$WALLPAPER_DIR"
    cat > "$WALLPAPER_FILE" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#08111f"/>
      <stop offset="48%" stop-color="#10233d"/>
      <stop offset="100%" stop-color="#03060d"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#8ff5d2" stop-opacity="0.85"/>
      <stop offset="45%" stop-color="#2fd6a3" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#2fd6a3" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="ring" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#74e7ff" stop-opacity="0.15"/>
      <stop offset="50%" stop-color="#79f2ca" stop-opacity="0.8"/>
      <stop offset="100%" stop-color="#74e7ff" stop-opacity="0.15"/>
    </linearGradient>
  </defs>

  <rect width="1600" height="900" fill="url(#bg)"/>
  <circle cx="1240" cy="140" r="280" fill="#0b1730" opacity="0.45"/>
  <circle cx="1220" cy="170" r="250" fill="#163052" opacity="0.18"/>
  <circle cx="400" cy="680" r="420" fill="#0c1b32" opacity="0.35"/>
  <circle cx="820" cy="420" r="170" fill="url(#glow)"/>
  <ellipse cx="820" cy="420" rx="280" ry="120" fill="none" stroke="url(#ring)" stroke-width="3"/>
  <ellipse cx="820" cy="420" rx="355" ry="160" fill="none" stroke="#5ce3c2" stroke-opacity="0.12" stroke-width="1.5"/>
  <circle cx="1010" cy="360" r="8" fill="#8ff5d2"/>
  <circle cx="1080" cy="470" r="5" fill="#74e7ff" opacity="0.7"/>

  <g fill="#f4f7fb">
    <text x="120" y="380" font-family="Cantarell, Arial, sans-serif" font-size="78" font-weight="700" letter-spacing="1">
      Rescue System
    </text>
    <text x="124" y="438" font-family="Cantarell, Arial, sans-serif" font-size="24" font-weight="400" fill="#a6b9ce">
      Backup, restore and partitioning without the boot log noise.
    </text>
  </g>

  <rect x="120" y="500" width="210" height="4" rx="2" fill="#79f2ca" opacity="0.9"/>
</svg>
EOF
    ok "Wallpaper ready"
}

ensure_user() {
    log "Ensuring rescue user..."
    getent group sudo >/dev/null 2>&1 || groupadd -r sudo
    getent group autologin >/dev/null 2>&1 || groupadd -r autologin

    if ! id -u "$REAL_USER" >/dev/null 2>&1; then
        useradd -m -s /bin/bash -G sudo,autologin "$REAL_USER"
        ok "User $REAL_USER created"
    fi

    usermod -aG sudo,autologin,audio,video,plugdev,netdev "$REAL_USER" 2>/dev/null || true
    echo "$REAL_USER:diskadmin" | chpasswd

    install -d -m 0750 /etc/sudoers.d
    cat > "/etc/sudoers.d/99_$REAL_USER" <<EOF
$REAL_USER ALL=(ALL) NOPASSWD:ALL
EOF
    chmod 0440 "/etc/sudoers.d/99_$REAL_USER"
    ok "User ready"
}

configure_locale_keyboard() {
    log "Configuring locale and keyboard..."
    if [ -f /etc/locale.gen ]; then
        if grep -Eq '^[#[:space:]]*en_US.UTF-8 UTF-8' /etc/locale.gen; then
            sed -i 's/^[#[:space:]]*en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
        else
            echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen
        fi
    fi

    have locale-gen && locale-gen en_US.UTF-8 >/dev/null 2>&1 || true
    update-locale LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8 2>/dev/null || true

    cat > /etc/default/locale <<'EOF'
LANG=en_US.UTF-8
LANGUAGE=en_US:en
LC_ALL=en_US.UTF-8
EOF

    cat > /etc/default/keyboard <<'EOF'
XKBMODEL="pc105"
XKBLAYOUT="de"
XKBVARIANT=""
XKBOPTIONS=""
BACKSPACE="guess"
EOF
    ok "Locale and keyboard configured"
}

configure_lightdm() {
    log "Configuring LightDM..."
    mkdir -p /etc/lightdm/lightdm.conf.d
    rm -f /etc/lightdm/lightdm.conf.d/50-autologin.conf /etc/lightdm/lightdm.conf.d/50-xfce.conf

    cat > /etc/lightdm/lightdm.conf.d/50-rescue.conf <<EOF
[Seat:*]
autologin-user=$REAL_USER
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
EOF
    install -m 0644 /etc/lightdm/lightdm.conf.d/50-rescue.conf /etc/lightdm/lightdm.conf

    cat > /etc/lightdm/lightdm-gtk-greeter.conf <<EOF
[greeter]
theme-name=$GTK_THEME
icon-theme-name=$ICON_THEME
font-name=${UI_FONT% *} 11
cursor-theme-name=Adwaita
cursor-theme-size=24
background=$WALLPAPER_FILE
user-background=false
default-user-image=#avatar-default
hide-user-image=false
position=50%,center 50%,center
clock-format=%H:%M
panel-position=bottom
indicators=~host;~spacer;~clock;~spacer;~power
screensaver-timeout=0
EOF

    echo "/usr/sbin/lightdm" > /etc/X11/default-display-manager
    echo "lightdm shared/default-x-display-manager select lightdm" | debconf-set-selections 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive dpkg-reconfigure lightdm 2>/dev/null || true
    maybe_systemctl enable lightdm
    maybe_systemctl set-default graphical.target
    ok "LightDM configured"
}

configure_live_session() {
    log "Configuring live session defaults..."
    mkdir -p /etc/live/config.conf.d /lib/live/config

    cat > /etc/live/config.conf.d/rescue.conf <<EOF
LIVE_USER="$REAL_USER"
LIVE_USER_FULLNAME="Rescue System Admin"
LIVE_USER_DEFAULT_GROUPS="sudo,autologin,audio,video,plugdev,netdev"
EOF

    cat > /lib/live/config/9990-rescue-user <<EOF
#!/bin/sh
set -e
REAL_USER="$REAL_USER"
getent group autologin >/dev/null 2>&1 || groupadd -r autologin || true
if ! id -u "\$REAL_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo,autologin "\$REAL_USER" || true
fi
usermod -aG sudo,autologin,audio,video,plugdev,netdev "\$REAL_USER" 2>/dev/null || true
mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/90-rescue-live.conf <<'LIVECONF'
[Seat:*]
autologin-user=$REAL_USER
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
LIVECONF
EOF
    chmod 0755 /lib/live/config/9990-rescue-user
    ok "Live session defaults configured"
}

configure_tty_fallback() {
    log "Configuring tty fallback..."
    mkdir -p /etc/systemd/system/getty@tty1.service.d
    cat > /etc/systemd/system/getty@tty1.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $REAL_USER --noclear %I \$TERM
EOF

    cat > "$REAL_HOME/.xinitrc" <<'EOF'
exec startxfce4
EOF

    cat > "$REAL_HOME/.profile" <<'EOF'
if [ -n "$BASH_VERSION" ] && [ -f "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi

if [ -z "${DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx
fi
EOF

    chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.xinitrc" "$REAL_HOME/.profile"
    maybe_systemctl daemon-reload
    ok "tty fallback configured"
}

configure_xfce() {
    log "Configuring XFCE..."
    local user_xfce_dir="$REAL_HOME/.config/xfce4/xfconf/xfce-perchannel-xml"
    local system_xfce_dir="/etc/xdg/xfce4/xfconf/xfce-perchannel-xml"
    local gtk3_dir="$REAL_HOME/.config/gtk-3.0"

    mkdir -p "$user_xfce_dir" "$system_xfce_dir" "$gtk3_dir"

    cat > "$user_xfce_dir/xsettings.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
  <property name="Net" type="empty">
    <property name="ThemeName" type="string" value="$GTK_THEME"/>
    <property name="IconThemeName" type="string" value="$ICON_THEME"/>
    <property name="CursorThemeName" type="string" value="Adwaita"/>
    <property name="EnableEventSounds" type="bool" value="false"/>
    <property name="EnableInputFeedbackSounds" type="bool" value="false"/>
  </property>
  <property name="Gtk" type="empty">
    <property name="FontName" type="string" value="$UI_FONT"/>
    <property name="MonospaceFontName" type="string" value="$MONO_FONT"/>
    <property name="CursorThemeName" type="string" value="Adwaita"/>
    <property name="CursorThemeSize" type="int" value="24"/>
  </property>
  <property name="Xft" type="empty">
    <property name="Antialias" type="int" value="1"/>
    <property name="HintStyle" type="string" value="hintslight"/>
    <property name="RGBA" type="string" value="rgb"/>
  </property>
</channel>
EOF

    cat > "$user_xfce_dir/xfwm4.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="theme" type="string" value="Default"/>
    <property name="title_font" type="string" value="Cantarell Bold 10"/>
    <property name="button_layout" type="string" value="O|HMC"/>
    <property name="snap_to_border" type="bool" value="true"/>
    <property name="snap_to_windows" type="bool" value="true"/>
    <property name="tile_on_move" type="bool" value="true"/>
    <property name="wrap_windows" type="bool" value="false"/>
    <property name="use_compositing" type="bool" value="true"/>
    <property name="workspace_count" type="int" value="1"/>
  </property>
</channel>
EOF

    cat > "$user_xfce_dir/xfce4-desktop.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-desktop" version="1.0">
  <property name="desktop-icons" type="empty">
    <property name="style" type="int" value="2"/>
    <property name="icon-size" type="uint" value="70"/>
    <property name="file-icons" type="empty">
      <property name="show-home" type="bool" value="true"/>
      <property name="show-filesystem" type="bool" value="false"/>
      <property name="show-trash" type="bool" value="false"/>
      <property name="show-removable" type="bool" value="true"/>
    </property>
  </property>
  <property name="desktop-menu" type="empty">
    <property name="show-windowlist" type="bool" value="false"/>
    <property name="show-desktop-menu" type="bool" value="false"/>
  </property>
  <property name="backdrop" type="empty">
    <property name="screen0" type="empty">
      <property name="monitorscreen" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="$WALLPAPER_FILE"/>
        </property>
      </property>
    </property>
  </property>
</channel>
EOF

    cat > "$user_xfce_dir/xfce4-panel.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array">
    <value type="int" value="1"/>
    <property name="dark-mode" type="bool" value="true"/>
    <property name="panel-1" type="empty">
      <property name="position" type="string" value="p=8;x=0;y=0"/>
      <property name="length" type="uint" value="100"/>
      <property name="position-locked" type="bool" value="true"/>
      <property name="icon-size" type="uint" value="28"/>
      <property name="size" type="uint" value="50"/>
      <property name="background-style" type="uint" value="1"/>
      <property name="background-rgba" type="array">
        <value type="double" value="0.023529"/>
        <value type="double" value="0.047059"/>
        <value type="double" value="0.090196"/>
        <value type="double" value="0.960000"/>
      </property>
      <property name="plugin-ids" type="array">
        <value type="int" value="1"/>
        <value type="int" value="2"/>
        <value type="int" value="3"/>
        <value type="int" value="4"/>
        <value type="int" value="5"/>
        <value type="int" value="6"/>
        <value type="int" value="7"/>
      </property>
    </property>
  </property>
  <property name="plugins" type="empty">
    <property name="plugin-1" type="string" value="applicationsmenu">
      <property name="button-title" type="string" value=""/>
      <property name="button-icon" type="string" value="xfce4_xicon4"/>
      <property name="show-button-title" type="bool" value="false"/>
      <property name="small" type="bool" value="false"/>
    </property>
    <property name="plugin-2" type="string" value="separator">
      <property name="style" type="uint" value="0"/>
    </property>
    <property name="plugin-3" type="string" value="tasklist">
      <property name="show-labels" type="bool" value="false"/>
      <property name="flat-buttons" type="bool" value="true"/>
      <property name="show-handle" type="bool" value="false"/>
    </property>
    <property name="plugin-4" type="string" value="separator">
      <property name="expand" type="bool" value="true"/>
      <property name="style" type="uint" value="0"/>
    </property>
    <property name="plugin-5" type="string" value="systray">
      <property name="square-icons" type="bool" value="true"/>
    </property>
    <property name="plugin-6" type="string" value="clock">
      <property name="digital-layout" type="uint" value="3"/>
      <property name="digital-time-font" type="string" value="Cantarell Bold 10"/>
      <property name="digital-date-font" type="string" value="Cantarell 8"/>
      <property name="digital-date-format" type="string" value="%d. %b %Y"/>
    </property>
    <property name="plugin-7" type="string" value="actions">
      <property name="appearance" type="uint" value="0"/>
      <property name="items" type="array">
        <value type="string" value="+shutdown"/>
      </property>
    </property>
  </property>
</channel>
EOF

    cat > "$user_xfce_dir/xfce4-keyboard-shortcuts.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-keyboard-shortcuts" version="1.0">
  <property name="commands" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Super&gt;t" type="string" value="xfce4-terminal"/>
      <property name="&lt;Super&gt;e" type="string" value="thunar"/>
      <property name="&lt;Super&gt;b" type="string" value="/usr/local/bin/systembu"/>
      <property name="&lt;Super&gt;p" type="string" value="/usr/local/bin/systempart"/>
      <property name="&lt;Super&gt;d" type="string" value="xfce4-display-settings"/>
      <property name="Print" type="string" value="xfce4-screenshooter"/>
    </property>
  </property>
  <property name="xfwm4" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Alt&gt;F4" type="string" value="close_window_key"/>
      <property name="&lt;Alt&gt;Tab" type="string" value="cycle_windows_key"/>
      <property name="&lt;Super&gt;Left" type="string" value="tile_left_key"/>
      <property name="&lt;Super&gt;Right" type="string" value="tile_right_key"/>
      <property name="&lt;Super&gt;Up" type="string" value="maximize_window_key"/>
      <property name="&lt;Super&gt;Down" type="string" value="hide_window_key"/>
    </property>
  </property>
</channel>
EOF

    cat > "$user_xfce_dir/xfce4-terminal.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-terminal" version="1.0">
  <property name="font-name" type="string" value="$MONO_FONT"/>
  <property name="font-use-system" type="bool" value="false"/>
  <property name="misc-menubar-default" type="bool" value="false"/>
  <property name="scrolling-unlimited" type="bool" value="true"/>
  <property name="color-foreground" type="string" value="#d6dde8"/>
  <property name="color-background" type="string" value="#08111f"/>
  <property name="background-mode" type="uint" value="0"/>
</channel>
EOF

    install -m 0644 "$user_xfce_dir/"*.xml "$system_xfce_dir/"

    cat > "$gtk3_dir/settings.ini" <<EOF
[Settings]
gtk-theme-name=$GTK_THEME
gtk-icon-theme-name=$ICON_THEME
gtk-font-name=$UI_FONT
gtk-cursor-theme-name=Adwaita
gtk-application-prefer-dark-theme=1
gtk-decoration-layout=:minimize,maximize,close
EOF

    cat > "$REAL_HOME/.gtkrc-2.0" <<EOF
gtk-theme-name="$GTK_THEME"
gtk-icon-theme-name="$ICON_THEME"
gtk-font-name="$UI_FONT"
gtk-cursor-theme-name="Adwaita"
EOF

    chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/.config" "$REAL_HOME/.gtkrc-2.0"
    ok "XFCE configured"
}

configure_launchers_and_menu() {
    log "Configuring launchers and menu..."
    mkdir -p /usr/share/applications /etc/xdg/menus /etc/xdg/xfce4/kiosk

    cat > /usr/share/applications/rescue-xfce-settings.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Settings
Comment=Open XFCE Settings Manager
Exec=xfce4-settings-manager
Icon=preferences-system
Terminal=false
Categories=Settings;
EOF

    cat > /etc/xdg/xfce4/kiosk/kioskrc <<'EOF'
[xfce4-panel]
CustomizePanel=false
EOF

    cat > /etc/xdg/menus/xfce-applications.menu <<'EOF'
<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"
  "http://www.freedesktop.org/standards/menu-spec/menu-1.0.dtd">
<Menu>
  <Name>Xfce</Name>
  <DefaultAppDirs/>
  <DefaultDirectoryDirs/>
  <Include>
    <Filename>xfce4-terminal.desktop</Filename>
    <Filename>thunar.desktop</Filename>
    <Filename>systembu.desktop</Filename>
    <Filename>systempart.desktop</Filename>
    <Filename>rescue-xfce-settings.desktop</Filename>
  </Include>
</Menu>
EOF

    cat > /usr/local/bin/rescue-trust-launcher.sh <<'EOF'
#!/bin/sh
launcher="${1:-}"
[ -n "$launcher" ] || exit 0
[ -f "$launcher" ] || exit 0

chmod 0755 "$launcher" 2>/dev/null || true
checksum="$(sha256sum "$launcher" | awk '{print $1}')"
[ -n "$checksum" ] || exit 0
gio set "$launcher" metadata::xfce-exe-checksum "$checksum" 2>/dev/null || true
gio set "$launcher" metadata::trusted true 2>/dev/null || true
EOF
    chmod 0755 /usr/local/bin/rescue-trust-launcher.sh

    cat > /usr/local/bin/rescue-trust-desktop-launchers.sh <<'EOF'
#!/bin/sh
for launcher in "$HOME"/Desktop/system*.desktop; do
    [ -f "$launcher" ] || continue
    /usr/local/bin/rescue-trust-launcher.sh "$launcher"
done
EOF
    chmod 0755 /usr/local/bin/rescue-trust-desktop-launchers.sh

    for base in "$REAL_HOME" "/root" "/etc/skel"; do
        mkdir -p "$base/Desktop"
    done

    for launcher in systembu systempart; do
        if [ ! -f "/usr/share/applications/$launcher.desktop" ]; then
            warn "$launcher.desktop is missing; package not installed yet"
            continue
        fi
        install -m 0755 "/usr/share/applications/$launcher.desktop" "$REAL_HOME/Desktop/$launcher.desktop"
        install -m 0755 "/usr/share/applications/$launcher.desktop" "/root/Desktop/$launcher.desktop"
        install -m 0755 "/usr/share/applications/$launcher.desktop" "/etc/skel/Desktop/$launcher.desktop"
    done

    if have dbus-launch; then
        for launcher in "$REAL_HOME"/Desktop/system*.desktop; do
            [ -f "$launcher" ] || continue
            su - "$REAL_USER" -c "dbus-launch /usr/local/bin/rescue-trust-launcher.sh '$launcher'" 2>/dev/null || true
        done
        for launcher in /root/Desktop/system*.desktop; do
            [ -f "$launcher" ] || continue
            dbus-launch /usr/local/bin/rescue-trust-launcher.sh "$launcher" 2>/dev/null || true
        done
    fi

    chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/Desktop"
    ok "Launchers and menu configured"
}

configure_user_dirs() {
    log "Configuring user directories..."
    for home in "$REAL_HOME" "/root" "/etc/skel"; do
        mkdir -p "$home/.config"
        cat > "$home/.config/user-dirs.dirs" <<'EOF'
XDG_DESKTOP_DIR="$HOME/Desktop"
XDG_DOWNLOAD_DIR="$HOME"
XDG_TEMPLATES_DIR="$HOME"
XDG_PUBLICSHARE_DIR="$HOME"
XDG_DOCUMENTS_DIR="$HOME"
XDG_MUSIC_DIR="$HOME"
XDG_PICTURES_DIR="$HOME"
XDG_VIDEOS_DIR="$HOME"
EOF
        for dir in Documents Downloads Music Pictures Public Templates Videos; do
            rmdir "$home/$dir" 2>/dev/null || true
        done
    done
    sed -i 's/^enabled=True/enabled=False/' /etc/xdg/user-dirs.conf 2>/dev/null || true
    chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.config/user-dirs.dirs"
    ok "User directories configured"
}

configure_power() {
    log "Disabling lock, sleep and display blanking..."
    mkdir -p /etc/xdg/autostart /etc/systemd/logind.conf.d

    cat > /usr/local/bin/rescue-disable-display-sleep.sh <<'EOF'
#!/bin/sh
xset s off
xset s noblank
xset -dpms
EOF
    chmod 0755 /usr/local/bin/rescue-disable-display-sleep.sh

    cat > /etc/xdg/autostart/xfce4-power-manager.desktop <<'EOF'
[Desktop Entry]
Type=Application
Hidden=true
EOF

    cat > /etc/xdg/autostart/xfce4-screensaver.desktop <<'EOF'
[Desktop Entry]
Type=Application
Hidden=true
EOF

    cat > /etc/xdg/autostart/light-locker.desktop <<'EOF'
[Desktop Entry]
Type=Application
Hidden=true
EOF

    cat > /etc/xdg/autostart/rescue-disable-display-sleep.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Disable Display Sleep
Exec=/usr/local/bin/rescue-disable-display-sleep.sh
OnlyShowIn=XFCE;
X-GNOME-Autostart-enabled=true
EOF

    cat > /etc/xdg/autostart/rescue-trust-launchers.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Trust Desktop Launchers
Exec=/usr/local/bin/rescue-trust-desktop-launchers.sh
OnlyShowIn=XFCE;
X-GNOME-Autostart-enabled=true
EOF

    cat > /etc/systemd/logind.conf.d/90-rescue-no-sleep.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
IdleAction=ignore
EOF

    maybe_systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
    maybe_systemctl restart systemd-logind
    ok "Power management configured"
}

configure_network_default_off() {
    log "Setting network default to off..."
    cat > /usr/local/bin/rescue-network.sh <<'EOF'
#!/bin/sh
set -eu
cmd="${1:-status}"
case "$cmd" in
    on|enable|up) nmcli networking on ;;
    off|disable|down) nmcli networking off ;;
    toggle)
        state="$(nmcli networking 2>/dev/null || true)"
        if [ "$state" = "enabled" ]; then
            nmcli networking off
        else
            nmcli networking on
        fi
        ;;
    status) nmcli general status ;;
    *)
        echo "Usage: $0 {on|off|toggle|status}"
        exit 2
        ;;
esac
EOF
    chmod 0755 /usr/local/bin/rescue-network.sh

    cat > /etc/systemd/system/rescue-network-off.service <<'EOF'
[Unit]
Description=Disable networking by default at boot
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/rescue-network.sh off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    maybe_systemctl daemon-reload
    maybe_systemctl enable rescue-network-off.service
    maybe_systemctl start rescue-network-off.service
    ok "Network default configured"
}

configure_plymouth() {
    log "Configuring Plymouth splash..."
    if have plymouth-set-default-theme; then
        plymouth-set-default-theme -R spinfinity 2>/dev/null || \
        plymouth-set-default-theme spinfinity 2>/dev/null || true
    fi
    have update-initramfs && update-initramfs -u 2>/dev/null || true
    ok "Plymouth configured"
}

configure_grub() {
    log "Configuring GRUB quiet boot..."
    if [ ! -f /etc/default/grub ]; then
        warn "/etc/default/grub not found; skipping installed-system GRUB tuning"
        return
    fi

    sed -i \
        -e '/^GRUB_DEFAULT=/d' \
        -e '/^GRUB_TIMEOUT_STYLE=/d' \
        -e '/^GRUB_TIMEOUT=/d' \
        -e '/^GRUB_RECORDFAIL_TIMEOUT=/d' \
        -e '/^GRUB_CMDLINE_LINUX_DEFAULT=/d' \
        /etc/default/grub

    cat >> /etc/default/grub <<EOF

GRUB_DEFAULT=0
GRUB_TIMEOUT_STYLE=hidden
GRUB_TIMEOUT=0
GRUB_RECORDFAIL_TIMEOUT=0
GRUB_CMDLINE_LINUX_DEFAULT="$QUIET_BOOT_ARGS"
EOF

    if have update-grub; then
        update-grub 2>/dev/null || true
    elif have grub-mkconfig && [ -d /boot/grub ]; then
        grub-mkconfig -o /boot/grub/grub.cfg 2>/dev/null || true
    fi
    ok "GRUB configured"
}

sync_skel() {
    log "Syncing default user skeleton..."
    mkdir -p /etc/skel/.config
    rm -rf /etc/skel/.config/xfce4 /etc/skel/.config/gtk-3.0
    cp -r "$REAL_HOME/.config/xfce4" /etc/skel/.config/
    cp -r "$REAL_HOME/.config/gtk-3.0" /etc/skel/.config/
    install -m 0644 "$REAL_HOME/.gtkrc-2.0" /etc/skel/.gtkrc-2.0
    install -m 0644 "$REAL_HOME/.profile" /etc/skel/.profile
    install -m 0644 "$REAL_HOME/.xinitrc" /etc/skel/.xinitrc
    chown -R root:root /etc/skel
    ok "Skeleton synced"
}

main() {
    need_root
    write_wallpaper
    ensure_user
    configure_locale_keyboard
    configure_lightdm
    configure_live_session
    configure_tty_fallback
    configure_xfce
    configure_launchers_and_menu
    configure_user_dirs
    configure_power
    configure_network_default_off
    configure_plymouth
    configure_grub
    sync_skel

    echo
    ok "Rescue desktop ready"
    printf '  User:   %s\n' "$REAL_USER"
    printf '  Theme:  %s + %s\n' "$GTK_THEME" "$ICON_THEME"
    printf '  Tools:  Desktop and start menu launchers available\n'
    printf '  Boot:   Plymouth splash with quiet kernel parameters\n'
    echo
}

main "$@"
