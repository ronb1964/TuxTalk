/* shellIndicator.js
 *
 * TuxTalk's recording indicator, drawn inside GNOME Shell.
 *
 * The app's own indicator is an X11 window, because only XWayland lets an
 * app place a window. Mutter scales that window from XWayland's resolution,
 * which blurs it on a fractionally scaled monitor. Drawn here it renders at
 * the monitor's own scale, and the Shell knows the focused window, so it
 * appears on the screen being dictated into and follows the focus there.
 * The dictation service drives it over D-Bus (shell_indicator.py); the
 * drawing mirrors recording_indicator.py and indicator_styles.py.
 */

import Cairo from 'cairo';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'io.github.ronb1964.TalkType.Indicator';
const OBJECT_PATH = '/io/github/ronb1964/TalkType/Indicator';

const IndicatorIface = `
<node>
  <interface name="io.github.ronb1964.TalkType.Indicator">
    <method name="Show">
      <arg type="a{sv}" direction="in" name="settings"/>
    </method>
    <method name="SetRecording">
      <arg type="b" direction="in" name="recording"/>
    </method>
    <method name="Update">
      <arg type="d" direction="in" name="level"/>
      <arg type="ad" direction="in" name="values"/>
    </method>
    <method name="Hide"/>
  </interface>
</node>`;

const BASE_WIDTH = 200;
const BASE_HEIGHT = 180;
const EDGE_MARGIN = 20;
const FOLLOW_MARGIN = 40;
// Above the bottom edge, as a share of the window's height: clears a message
// box or status bar and keeps the indicator off the screen's lower edge.
const FOLLOW_BOTTOM_SHARE = 0.2;
const FOLLOW_TOP = 50;
const FOLLOW_SIDE = 20;

const CYAN = [0.30, 0.70, 1.0];
const BACKING_CORE = 0.50;
const RADIAL_MAX_R = 0.42;
const RADIAL_BASE = 0.42;

function clamp(value, low, high) {
    return Math.max(low, Math.min(value, high));
}

function horizontal(position, left, right, centre) {
    if (position.includes('left'))
        return left;
    if (position.includes('right'))
        return right;
    return centre;
}

function vertical(position, top, bottom, centre) {
    if (position.startsWith('top'))
        return top;
    if (position.startsWith('bottom'))
        return bottom;
    return centre;
}

function followPosition(win, width, height, monitor, position) {
    const x = horizontal(position, win.x + FOLLOW_SIDE, win.x + win.width - width - FOLLOW_SIDE,
        win.x + (win.width - width) / 2);
    const bottomGap = Math.max(FOLLOW_MARGIN, win.height * FOLLOW_BOTTOM_SHARE);
    const y = vertical(position, win.y + FOLLOW_TOP, win.y + win.height - height - bottomGap,
        win.y + (win.height - height) / 2);
    return [
        clamp(x, monitor.x, monitor.x + monitor.width - width),
        clamp(y, monitor.y, monitor.y + monitor.height - height),
    ];
}

function fixedPosition(position, width, height, monitor, offsetX, offsetY) {
    const x = monitor.x + offsetX + horizontal(position, EDGE_MARGIN, monitor.width - width - EDGE_MARGIN,
        (monitor.width - width) / 2);
    const y = monitor.y + offsetY + vertical(position, EDGE_MARGIN, monitor.height - height - EDGE_MARGIN,
        (monitor.height - height) / 2);
    return [
        clamp(x, monitor.x, monitor.x + monitor.width - width),
        clamp(y, monitor.y, monitor.y + monitor.height - height),
    ];
}

function mix(a, b, t) {
    return a.map((v, i) => v + (b[i] - v) * t);
}

// The classic cyan keeps the orb's hand-tuned gradient (null palette).
function orbPalette(rgb) {
    if (rgb.every((v, i) => Math.abs(v - CYAN[i]) <= 0.03))
        return null;
    const white = [1, 1, 1];
    const edge = mix(rgb, [0, 0, 0], 0.15);
    return {
        glow: rgb,
        core: [[0.0, white, 1.0], [0.4, mix(rgb, white, 0.55), 0.9], [0.7, rgb, 0.6],
            [0.9, edge, 0.3], [1.0, edge, 0.0]],
        near: mix(rgb, white, 0.5),
        far: rgb,
    };
}

function foreground(rgb, intensity, alpha = null) {
    const t = 0.35 * intensity;
    const [r, g, b] = rgb.map(v => v + (1 - v) * t);
    return [r, g, b, alpha ?? 0.82 + 0.18 * intensity];
}

function backingAlpha(r) {
    const plateau = 0.42;
    if (r <= plateau)
        return BACKING_CORE;
    return BACKING_CORE * (0.5 + 0.5 * Math.cos(Math.PI * (r - plateau) / (1 - plateau)));
}

function drawBacking(cr, cx, cy, rx, ry) {
    cr.save();
    cr.translate(cx, cy);
    cr.scale(rx, ry);
    const gradient = new Cairo.RadialGradient(0, 0, 0, 0, 0, 1);
    const stops = 96;
    for (let k = 0; k <= stops; k++)
        gradient.addColorStopRGBA(k / stops, 0.05, 0.06, 0.09, backingAlpha(k / stops));
    cr.setSource(gradient);
    cr.arc(0, 0, 1, 0, 2 * Math.PI);
    cr.fill();
    cr.restore();
}

function pillPath(cr, x, y, width, height) {
    const r = height / 2;
    cr.newSubPath();
    cr.arc(x + r, y + r, r, Math.PI, 3 * Math.PI / 2);
    cr.arc(x + width - r, y + r, r, 3 * Math.PI / 2, 0);
    cr.arc(x + width - r, y + height - r, r, 0, Math.PI / 2);
    cr.arc(x + r, y + height - r, r, Math.PI / 2, Math.PI);
    cr.closePath();
}

export class ShellIndicator {
    constructor() {
        this._settings = null;
        this._recording = false;
        this._level = 0;
        this._values = [];
        this._pulse = 0;
        this._particlePulse = 0;
        this._startTime = 0;
        this._tickId = 0;
        this._focusId = 0;

        this._area = new St.DrawingArea({reactive: false, visible: false});
        this._area.connect('repaint', area => this._repaint(area));
        // Above the windows; not reactive, so clicks go through to them.
        Main.layoutManager.uiGroup.add_child(this._area);

        this._dbus = Gio.DBusExportedObject.wrapJSObject(IndicatorIface, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null);
    }

    Show(settings) {
        const value = key => settings[key]?.deep_unpack();
        this._settings = {
            style: value('style') ?? 'orb',
            rgb: value('rgb') ?? CYAN,
            scale: value('scale') ?? 1.0,
            position: (value('position') ?? 'center').toLowerCase(),
            follow: value('follow') ?? false,
            offsetX: value('offset_x') ?? 0,
            offsetY: value('offset_y') ?? 0,
        };
        this._palette = orbPalette(this._settings.rgb);
        this._recording = false;
        this._level = 0;
        this._values = [];
        this._startTime = GLib.get_monotonic_time();
        this._area.set_size(BASE_WIDTH * this._settings.scale, BASE_HEIGHT * this._settings.scale);
        this._place();
        this._area.show();
        if (!this._focusId)
            this._focusId = global.display.connect('notify::focus-window', () => this._place());
        if (!this._tickId)
            this._tickId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => this._tick());
    }

    SetRecording(recording) {
        this._recording = recording;
        this._startTime = GLib.get_monotonic_time();
    }

    Update(level, values) {
        this._level = level;
        this._values = values;
    }

    Hide() {
        this._area.hide();
        this._stop();
        this._recording = false;
        this._values = [];
    }

    destroy() {
        this._stop();
        Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
        this._area.destroy();
    }

    _stop() {
        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }
        if (this._focusId) {
            global.display.disconnect(this._focusId);
            this._focusId = 0;
        }
    }

    _place() {
        const {position, follow, offsetX, offsetY} = this._settings;
        const [width, height] = this._area.get_size();
        const display = global.display;
        const win = display.focus_window;
        const monitor = display.get_monitor_geometry(win ? win.get_monitor() : display.get_current_monitor());
        const [x, y] = follow && win
            ? followPosition(win.get_frame_rect(), width, height, monitor, position)
            : fixedPosition(position, width, height, monitor, offsetX, offsetY);
        this._area.set_position(Math.round(x), Math.round(y));
    }

    _tick() {
        if (!this._recording) {
            this._pulse = (this._pulse + 0.05) % (2 * Math.PI);
            this._particlePulse = (this._particlePulse + 0.03) % (2 * Math.PI);
        }
        this._area.queue_repaint();
        return GLib.SOURCE_CONTINUE;
    }

    _repaint(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        cr.setOperator(Cairo.Operator.CLEAR);
        cr.paint();
        cr.setOperator(Cairo.Operator.OVER);
        const {style, rgb} = this._settings;
        if (style === 'waveform')
            this._drawWaveform(cr, width, height, rgb);
        else if (style === 'bars')
            this._drawBars(cr, width, height, rgb);
        else if (style === 'radial')
            this._drawRadial(cr, width, height, rgb);
        else
            this._drawOrb(cr, width, height);
        cr.$dispose();
    }

    // values: the waveform's points, already scaled to -1..1 by the service.
    _drawWaveform(cr, w, h, rgb) {
        drawBacking(cr, w / 2, h / 2, w * 0.48, h * 0.44);
        const points = this._values;
        if (points.length === 0)
            return;
        const iw = w * 0.60;
        const x0 = (w - iw) / 2;
        const amp = (h * 0.46 / 2) * 0.9;
        cr.newPath();
        points.forEach((v, i) => cr.lineTo(x0 + i * iw / points.length, h / 2 - v * amp));
        cr.setSourceRGBA(...foreground(rgb, Math.min(1, this._level + 0.3)));
        cr.setLineWidth(2.6);
        cr.stroke();
    }

    // values: one magnitude (0..1) per spectrum band.
    _drawBars(cr, w, h, rgb) {
        drawBacking(cr, w / 2, h / 2, w * 0.49, h * 0.46);
        const bands = this._values;
        if (bands.length === 0)
            return;
        const iw = w * 0.52;
        const x0 = (w - iw) / 2;
        const gap = 4;
        const bw = (iw - gap * (bands.length - 1)) / bands.length;
        const ch = h * 0.40;
        const baseline = h / 2 + ch / 2;
        bands.forEach((mag, i) => {
            const bh = Math.max(2, mag * ch);
            cr.setSourceRGBA(...foreground(rgb, mag, 0.64 + 0.36 * mag));
            cr.rectangle(x0 + i * (bw + gap), baseline - bh, bw, bh);
            cr.fill();
        });
    }

    // values: how far (0..1) each point around the ring pushes outward.
    _drawRadial(cr, w, h, rgb) {
        const cx = w / 2;
        const cy = h / 2;
        const maxR = Math.min(w, h) * RADIAL_MAX_R;
        drawBacking(cr, cx, cy, maxR * 1.15, maxR * 1.15);
        const offsets = this._values;
        if (offsets.length === 0)
            return;
        const base = maxR * RADIAL_BASE;
        const level = this._level;
        cr.newPath();
        offsets.forEach((d, i) => {
            const angle = (i / offsets.length) * 2 * Math.PI - Math.PI / 2;
            const r = base + d * (maxR - base);
            cr.lineTo(cx + Math.cos(angle) * r, cy + Math.sin(angle) * r);
        });
        cr.closePath();
        cr.setSourceRGBA(...foreground(rgb, level, 0.18 + 0.12 * level));
        cr.fillPreserve();
        cr.setSourceRGBA(...foreground(rgb, Math.min(1, level + 0.3)));
        cr.setLineWidth(2.2 + maxR * 0.015);
        cr.stroke();
        cr.setSourceRGBA(...foreground(rgb, level, 0.8));
        cr.arc(cx, cy, base * (0.6 + 0.15 * level), 0, 2 * Math.PI);
        cr.fill();
    }

    _drawOrb(cr, w, h) {
        const scale = this._settings.scale;
        const x = w * 0.3;
        const y = h * 0.5;
        const pillHeight = 55 * scale;
        pillPath(cr, x - 32 * scale, y - pillHeight / 2, 130 * scale, pillHeight);
        cr.setSourceRGBA(0.1, 0.1, 0.15, 0.85);
        if (this._recording) {
            cr.fillPreserve();
            cr.setSourceRGBA(1.0, 0.2, 0.2, 0.9);
            cr.setLineWidth(2.5 * scale);
            cr.stroke();
        } else {
            cr.fill();
        }
        this._drawOrbCore(cr, x, y, 15 * scale);
        this._drawParticles(cr, x, y, 15 * scale * 1.8);
        const elapsed = Math.floor((GLib.get_monotonic_time() - this._startTime) / 1e6);
        this._drawTimer(cr, x + 32 * scale, y, elapsed);
    }

    _drawOrbCore(cr, x, y, baseSize) {
        const palette = this._palette;
        const size = this._recording ? baseSize : baseSize * (1 + 0.1 * Math.sin(this._pulse));
        const glow = palette ? palette.glow : CYAN;
        for (let i = 0; i < 3; i++) {
            const radius = size * (1.5 - i * 0.15);
            const strength = this._recording ? this._level : 1;
            const gradient = new Cairo.RadialGradient(x, y, size * 0.5, x, y, radius);
            gradient.addColorStopRGBA(0, ...glow, 0.4 * strength * (1 - i * 0.3));
            gradient.addColorStopRGBA(1, ...glow, 0);
            cr.setSource(gradient);
            cr.arc(x, y, radius, 0, 2 * Math.PI);
            cr.fill();
        }
        const core = new Cairo.RadialGradient(x, y, 0, x, y, size * 1.3);
        const stops = palette ? palette.core : [[0, [1, 1, 1], 1], [0.4, [0.5, 0.9, 1], 0.9],
            [0.7, [0.2, 0.6, 0.9], 0.6], [0.9, [0.2, 0.5, 0.8], 0.3], [1, [0.2, 0.5, 0.8], 0]];
        for (const [stop, color, alpha] of stops)
            core.addColorStopRGBA(stop, ...color, alpha);
        cr.setSource(core);
        cr.arc(x, y, size * 1.3, 0, 2 * Math.PI);
        cr.fill();
    }

    _drawParticles(cr, cx, cy, baseRadius) {
        const palette = this._palette;
        const extend = this._recording ? this._level : 0;
        const distance = 0.7 + 0.7 * extend;
        for (let i = 0; i < 16; i++) {
            const angle = i * 2 * Math.PI / 16;
            const px = cx + baseRadius * distance * Math.cos(angle);
            const py = cy + baseRadius * distance * Math.sin(angle);
            const size = this._recording
                ? 4 + this._level * 2
                : 4 * (1 + 0.15 * Math.sin(this._particlePulse + i * 0.4));
            const [r, g, b] = palette
                ? mix(palette.near, palette.far, extend)
                : [0.7 - extend * 0.3, 0.95 - extend * 0.35, 1.0];
            const alpha = this._recording ? 0.6 + this._level * 0.3 : 0.6;
            const gradient = new Cairo.RadialGradient(px, py, 0, px, py, size * 1.5);
            gradient.addColorStopRGBA(0, r, g, b, alpha);
            gradient.addColorStopRGBA(1, r * 0.7, g * 0.7, b * 0.7, 0);
            cr.setSource(gradient);
            cr.arc(px, py, size * 1.5, 0, 2 * Math.PI);
            cr.fill();
        }
    }

    _drawTimer(cr, x, y, seconds) {
        const scale = this._settings.scale;
        const text = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
        cr.selectFontFace('Sans', Cairo.FontSlant.NORMAL, Cairo.FontWeight.BOLD);
        cr.setFontSize(22 * scale);
        const textY = y + cr.textExtents(text).height / 2;
        cr.setSourceRGBA(0, 0, 0, 0.5);
        cr.moveTo(x + 2 * scale, textY + 2 * scale);
        cr.showText(text);
        cr.setSourceRGBA(0.9, 0.9, 0.9, 1);
        cr.moveTo(x, textY);
        cr.showText(text);
    }
}
