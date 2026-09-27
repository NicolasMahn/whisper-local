import Gio from 'gi://Gio';
import GioUnix from 'gi://GioUnix';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {Overlay} from './overlay.js';

const BUS_NAME = 'io.github.nicolasmahn.WhisperLocal';
const OBJECT_PATH = '/io/github/nicolasmahn/WhisperLocal';
const DESKTOP_ID = `${BUS_NAME}.desktop`;

const INTERFACE_XML = `
<node>
  <interface name="io.github.nicolasmahn.WhisperLocal">
    <property name="State" type="s" access="read"/>
    <property name="Enabled" type="b" access="readwrite"/>
    <property name="Key" type="s" access="read"/>
    <property name="HandsFreeKey" type="s" access="read"/>
    <property name="Recent" type="as" access="read"/>
    <property name="Problem" type="s" access="read"/>
    <signal name="Level"><arg name="level" type="d"/></signal>
  </interface>
</node>`;

const WhisperProxy = Gio.DBusProxy.makeProxyWrapper(INTERFACE_XML);

const RECENT_COUNT = 5;
// The app clears a failed transcription's problem after 5 s; a lasting one
// (like missing keyboard access) should not keep the pill up forever.
const PROBLEM_SECONDS = 5;

// The one place that gives each app state its look. Hands-free records just
// like listening, so the panel icon treats them alike.
const LOOKS = {
    idle: {icon: 'idle', overlay: null},
    listening: {icon: 'listening', overlay: 'listening'},
    hands_free: {icon: 'listening', overlay: 'hands-free'},
    transcribing: {icon: 'transcribing', overlay: 'transcribing'},
};

// Mirrors KEY_NAMES in the app's settings window.
const KEY_NAMES = {
    right_ctrl: 'Right Ctrl',
    right_alt: 'Right Alt',
    right_cmd: 'Right Super',
    right_shift: 'Right Shift',
    f13: 'F13',
    f14: 'F14',
    f15: 'F15',
};

function keyName(key) {
    return KEY_NAMES[key] ?? key ?? 'the key';
}

function preview(text) {
    const characters = Array.from(text.replace(/\s+/g, ' ').trim());
    return characters.length > 70
        ? `${characters.slice(0, 69).join('')}…`
        : characters.join('');
}

const WhisperButton = GObject.registerClass(
class WhisperButton extends PanelMenu.Button {
    _init() {
        super._init(0.5, 'Whisper Local');

        this._icon = new St.Icon({
            iconName: 'audio-input-microphone-symbolic',
            styleClass: 'system-status-icon whisper-local-indicator whisper-local-disabled',
        });
        this.add_child(this._icon);

        this._status = new PopupMenu.PopupMenuItem('App not running', {reactive: false});
        this.menu.addMenuItem(this._status);

        this._enabled = new PopupMenu.PopupSwitchMenuItem('Enabled', false);
        this._enabledSignal = this._enabled.connect('toggled', (_item, state) =>
            this._setEnabled(state));
        this.menu.addMenuItem(this._enabled);

        this._problem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._problem.label.clutterText.lineWrap = true;
        this.menu.addMenuItem(this._problem);

        this._recentSeparator = new PopupMenu.PopupSeparatorMenuItem();
        this.menu.addMenuItem(this._recentSeparator);
        this._recent = Array.from({length: RECENT_COUNT}, (_, i) => {
            const item = new PopupMenu.PopupMenuItem('');
            item.label.styleClass = 'whisper-local-transcript';
            item.label.clutterText.singleLineMode = true;
            item.label.clutterText.ellipsize = Pango.EllipsizeMode.END;
            item.connect('activate', () => {
                St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD,
                    this._proxy?.Recent?.[i] ?? '');
            });
            this.menu.addMenuItem(item);
            return item;
        });

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._settings = new PopupMenu.PopupMenuItem('Open settings');
        this._settingsSignal = this._settings.connect('activate', () => this._openSettings());
        this.menu.addMenuItem(this._settings);

        this._overlay = new Overlay();

        this._sync();
        this._watchId = Gio.bus_watch_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            (connection, name, owner) => this._onAppeared(connection, name, owner),
            () => this._onVanished());
    }

    _onAppeared(connection, name, owner) {
        if (this._destroyed)
            return;

        this._disconnectProxy();
        this._owner = owner;
        this._connectFailed = false;
        this._sync();

        const pending = new Gio.Cancellable();
        this._pending = pending;
        new WhisperProxy(connection, name, OBJECT_PATH, (proxy, error) => {
            if (this._destroyed || this._pending !== pending || this._owner !== owner)
                return;

            this._pending = null;
            if (error) {
                logError(error);
                this._connectFailed = true;
            } else {
                this._proxy = proxy;
                this._proxySignal = proxy.connect('g-properties-changed', () => this._sync());
                this._levelSignal = proxy.connectSignal('Level', (_proxy, _sender, [level]) =>
                    this._overlay.setLevel(level));
            }
            this._sync();
        }, pending, Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES);
    }

    _onVanished() {
        if (this._destroyed)
            return;

        this._disconnectProxy();
        this._owner = null;
        this._sync();
    }

    _disconnectProxy() {
        this._pending?.cancel();
        this._pending = null;
        this._writePending?.cancel();
        this._writePending = null;
        if (this._proxySignal)
            this._proxy.disconnect(this._proxySignal);
        this._proxySignal = null;
        if (this._levelSignal)
            this._proxy.disconnectSignal(this._levelSignal);
        this._levelSignal = null;
        this._proxy = null;
        this._problemSeen = undefined;
        this._clearProblemFlash();
    }

    _sync() {
        const proxy = this._proxy;
        this._status.visible = !proxy;
        this._status.label.text = !this._owner ? 'App not running'
            : this._connectFailed ? 'Could not read app status' : 'Connecting…';
        this._enabled.visible = Boolean(proxy);
        this._enabled.sensitive = Boolean(proxy) && !this._writePending;
        this._problem.visible = Boolean(proxy?.Problem);
        const recent = proxy?.Recent ?? [];
        this._recentSeparator.visible = recent.length > 0;
        this._recent.forEach((item, i) => {
            item.visible = i < recent.length;
            item.label.text = i < recent.length ? preview(recent[i]) : '';
        });

        if (proxy) {
            if (!this._writePending) {
                this._updatingSwitch = true;
                try {
                    this._enabled.setToggleState(proxy.Enabled);
                } finally {
                    this._updatingSwitch = false;
                }
            }
            this._problem.label.text = proxy.Problem;
            this._trackProblem(proxy.Problem);
        }

        const look = LOOKS[proxy?.State] ?? LOOKS.idle;
        let icon = 'disabled';
        if (proxy?.Enabled)
            icon = proxy.Problem && look === LOOKS.idle ? 'problem' : look.icon;
        this._icon.styleClass = `system-status-icon whisper-local-indicator whisper-local-${icon}`;

        if (!proxy?.Enabled)
            this._overlay.present(null);
        else if (look.overlay === 'hands-free')
            this._overlay.present(look.overlay, `Tap ${keyName(proxy.HandsFreeKey || proxy.Key)} to finish · Esc to cancel`);
        else if (look.overlay)
            this._overlay.present(look.overlay);
        else if (this._problemFlashId)
            this._overlay.present('problem', proxy.Problem);
        else
            this._overlay.present(null);
    }

    // Flashes a problem in the pill when it appears, not one that was already
    // there when the extension connected.
    _trackProblem(problem) {
        if (problem === this._problemSeen)
            return;

        const connecting = this._problemSeen === undefined;
        this._problemSeen = problem;
        this._clearProblemFlash();
        if (!problem || connecting)
            return;

        this._problemFlashId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, PROBLEM_SECONDS, () => {
            this._problemFlashId = 0;
            this._sync();
            return GLib.SOURCE_REMOVE;
        });
    }

    _clearProblemFlash() {
        if (this._problemFlashId)
            GLib.source_remove(this._problemFlashId);
        this._problemFlashId = 0;
    }

    _setEnabled(state) {
        if (this._updatingSwitch || !this._proxy || this._writePending)
            return;

        const proxy = this._proxy;
        const pending = new Gio.Cancellable();
        this._writePending = pending;
        this._enabled.sensitive = false;
        proxy.call('org.freedesktop.DBus.Properties.Set',
            new GLib.Variant('(ssv)', [BUS_NAME, 'Enabled', new GLib.Variant('b', state)]),
            Gio.DBusCallFlags.NONE, -1, pending, (source, result) => {
                if (this._destroyed || this._proxy !== proxy || this._writePending !== pending)
                    return;

                this._writePending = null;
                this._enabled.sensitive = true;
                try {
                    source.call_finish(result);
                } catch (error) {
                    logError(error);
                    Main.notify('Whisper Local', 'Could not change dictation setting');
                }
                this._sync();
            });
    }

    _openSettings() {
        try {
            const app = GioUnix.DesktopAppInfo.new(DESKTOP_ID);
            if (!app) {
                Main.notify('Whisper Local', 'Settings app is not installed');
                return;
            }
            app.launch([], null);
        } catch (error) {
            logError(error);
            Main.notify('Whisper Local', 'Could not open settings');
        }
    }

    destroy() {
        this._destroyed = true;
        if (this._watchId)
            Gio.bus_unwatch_name(this._watchId);
        this._watchId = null;
        this._disconnectProxy();
        this._enabled.disconnect(this._enabledSignal);
        this._settings.disconnect(this._settingsSignal);
        this._overlay.destroy();
        this._overlay = null;
        super.destroy();
    }
});

export default class WhisperLocalExtension extends Extension {
    enable() {
        this._indicator = new WhisperButton();
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
