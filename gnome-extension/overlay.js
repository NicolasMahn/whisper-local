import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Layout from 'resource:///org/gnome/shell/ui/layout.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

// Centre bars reach higher, so the row reads as a voice rather than a meter.
const BAR_WEIGHTS = [0.55, 0.75, 0.9, 1, 0.9, 0.75, 0.55];
const BAR_REST = 4;
const BAR_PEAK = 26;
// Level puts room noise near 0.2 and normal speech around 0.6-0.8; stretching
// that span over the full bar range makes speech read as an obvious waveform.
const LEVEL_FLOOR = 0.2;
const LEVEL_CEILING = 0.75;
const FADE_MS = 150;
// Bars rise quickly with the voice and settle slowly, so steps between
// Level updates never show as jitter.
const ATTACK_SECONDS = 0.05;
const RELEASE_SECONDS = 0.18;
const BUSY_BLEND_SECONDS = 0.25;
const RIPPLE_SECONDS = 1.6;

/**
 * The pill at the bottom centre of the primary monitor. It never takes
 * focus or clicks. present(look, text) shows one of the looks 'listening',
 * 'hands-free', 'transcribing' or 'problem'; present(null) fades it out.
 */
export const Overlay = GObject.registerClass(
class Overlay extends St.Widget {
    _init() {
        super._init({
            x_expand: true,
            y_expand: true,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.END,
            visible: false,
            opacity: 0,
        });
        this.add_constraint(new Layout.MonitorConstraint({primary: true, workArea: true}));

        this._pill = new St.BoxLayout({style_class: 'whisper-local-pill'});
        this.add_child(this._pill);

        this._icon = new St.Icon({
            icon_name: 'dialog-warning-symbolic',
            style_class: 'whisper-local-pill-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._pill.add_child(this._icon);

        this._dot = new St.Widget({
            style_class: 'whisper-local-pill-dot',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._pill.add_child(this._dot);

        this._bars = new St.BoxLayout({
            style_class: 'whisper-local-bars',
            y_align: Clutter.ActorAlign.CENTER,
        });
        for (const _weight of BAR_WEIGHTS) {
            this._bars.add_child(new St.Widget({
                style_class: 'whisper-local-bar',
                y_align: Clutter.ActorAlign.CENTER,
            }));
        }
        this._pill.add_child(this._bars);

        this._label = new St.Label({
            style_class: 'whisper-local-pill-text',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._pill.add_child(this._label);

        this._look = null;
        this._level = 0;
        this._heights = BAR_WEIGHTS.map(() => 0);
        this._busy = 0;
        this._lastFrame = null;

        // Tied to this actor's frame clock and only started while visible.
        this._timeline = new Clutter.Timeline({actor: this, duration: 1000, repeat_count: -1});
        this._timeline.connect('new-frame', () => this._animate());
        this.connect('destroy', () => this._timeline.stop());

        Main.layoutManager.addTopChrome(this, {affectsInputRegion: false});
    }

    present(look, text = '') {
        if (!look) {
            this._fadeOut();
            return;
        }

        const recording = look === 'listening' || look === 'hands-free';
        const wasRecording = this._look === 'listening' || this._look === 'hands-free';
        if (recording && !(wasRecording && this.visible))
            this._level = 0;

        this._look = look;
        this._pill.style_class = `whisper-local-pill whisper-local-pill-${look}`;
        this._icon.visible = look === 'problem';
        this._dot.visible = recording;
        this._bars.visible = look !== 'problem';
        this._label.visible = Boolean(text);
        this._label.text = text;
        this._fadeIn();
    }

    setLevel(level) {
        const stretched = (level - LEVEL_FLOOR) / (LEVEL_CEILING - LEVEL_FLOOR);
        this._level = Math.min(Math.max(stretched, 0), 1);
    }

    _fadeIn() {
        if (!this.visible) {
            this.opacity = 0;
            this.show();
            this._lastFrame = null;
            this._timeline.start();
        }
        this.ease({opacity: 255, duration: FADE_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    _fadeOut() {
        if (!this.visible)
            return;

        this.ease({
            opacity: 0,
            duration: FADE_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                this.hide();
                this._timeline.stop();
            },
        });
    }

    _animate() {
        const now = GLib.get_monotonic_time() / GLib.USEC_PER_SEC;
        const elapsed = Math.min(now - (this._lastFrame ?? now), 0.1);
        this._lastFrame = now;

        const busy = this._look === 'transcribing';
        this._busy += ((busy ? 1 : 0) - this._busy) * (1 - Math.exp(-elapsed / BUSY_BLEND_SECONDS));

        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._bars.get_children().forEach((bar, i) => {
            // A slow ripple travelling left to right while transcribing.
            const wave = 0.5 + 0.5 * Math.sin(2 * Math.PI * (now / RIPPLE_SECONDS - i / BAR_WEIGHTS.length));
            // A gentle per-bar sway keeps a steady voice from looking frozen.
            const sway = 0.75 + 0.25 * Math.sin(now * 7 + i * 1.9);
            const target = busy ? 0.1 + 0.2 * wave : this._level * BAR_WEIGHTS[i] * sway;

            const seconds = target > this._heights[i] ? ATTACK_SECONDS : RELEASE_SECONDS;
            this._heights[i] += (target - this._heights[i]) * (1 - Math.exp(-elapsed / seconds));

            bar.height = Math.round((BAR_REST + this._heights[i] * (BAR_PEAK - BAR_REST)) * scale);
            bar.opacity = Math.round(255 - this._busy * 150 * (1 - wave));
        });
    }
});
