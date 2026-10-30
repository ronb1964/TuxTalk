// Stand-ins for what the TuxTalk GNOME extension uses of GNOME Shell and its
// gi:// libraries (loader.mjs hands them to it), and the controls a test
// drives it with: TuxTalk appearing on the bus, its replies and signals, the
// focused window, the menu. What the extension did to the desktop is kept in
// `shell` for the test to look at. Only as much of each API as the extension
// calls, behaving as GNOME 45 to 51 does.

export const shell = {
    logs: [],               // [level, text] from console.error and console.warn
    notifications: [],      // [title, body]
    grabs: new Map(),       // action -> accelerator, grabbed now
    keybindings: new Map(), // binding name -> the modes it's allowed in
    taken: new Set(),       // accelerators another shortcut already has
    timeouts: [],           // {id, callback} not run yet
    watchers: [],           // {name, appeared, vanished, watching}
    proxySetups: [],        // {callback, cancellable, flags, methods, signals} per proxy made
                            // (methods: name -> its in-args' types)
    statusArea: {},
    clipboard: null,
};

for (const level of ['error', 'warn']) {
    console[level] = (...parts) => shell.logs.push([level, parts.map(String).join(' ')]);
}

let lastId = 0;

/** connect, disconnect and emit, as GObject signals; a handler gets the
 * object first, then the signal's arguments. */
class Signals {
    connect(name, callback) {
        this._handlers ??= new Map();
        this._handlers.set(++lastId, [name, callback]);
        return lastId;
    }

    disconnect(id) {
        if (!this._handlers?.delete(id))
            throw new Error(`No handler with id ${id}`);
    }

    emit(name, ...args) {
        for (const [signal, callback] of [...(this._handlers?.values() ?? [])]) {
            if (signal === name)
                callback(this, ...args);
        }
    }
}

// --- gi:// libraries -------------------------------------------------------------

export const GLib = {
    PRIORITY_DEFAULT: 0,
    SOURCE_REMOVE: false,
    timeout_add(_priority, _ms, callback) {
        shell.timeouts.push({id: ++lastId, callback});
        return lastId;
    },
    timeout_add_seconds(priority, seconds, callback) {
        return GLib.timeout_add(priority, seconds * 1000, callback);
    },
    Source: {
        remove(id) {
            shell.timeouts = shell.timeouts.filter(t => t.id !== id);
        },
    },
};

export const GObject = {registerClass: klass => klass};

class Actor extends Signals {
    constructor(props = {}) {
        super();
        Object.assign(this, props);
    }
}

export const St = {
    Icon: Actor,
    ClipboardType: {CLIPBOARD: 0},
    Clipboard: {
        get_default: () => ({set_text: (_type, text) => (shell.clipboard = text)}),
    },
};

export const Pango = {EllipsizeMode: {NONE: 0}};

export const Meta = {
    KeyBindingFlags: {NONE: 0},
    KeyBindingAction: {NONE: 0},
    external_binding_name_for_action: action => `external-grab-${action}`,
};

export const Shell = {ActionMode: {NONE: 0, ALL: 0xffff}};

export const Gio = {
    DBusProxyFlags: {DO_NOT_AUTO_START: 1, DO_NOT_LOAD_PROPERTIES: 2},
    BusNameWatcherFlags: {NONE: 0},
    DBusError: {UNKNOWN_METHOD: 'UnknownMethod'},
    Cancellable: class {
        cancel() {
            this.cancelled = true;
        }

        is_cancelled() {
            return Boolean(this.cancelled);
        }
    },
    DBus: {
        session: {
            watch_name(name, _flags, appeared, vanished) {
                shell.watchers.push({name, appeared, vanished, watching: true});
                return shell.watchers.length;
            },
            unwatch_name(id) {
                shell.watchers[id - 1].watching = false;
            },
        },
    },
    DBusProxy: {
        // Like GJS's: the proxy has a NameRemote(...args, callback) for each
        // method the interface declares, and no other.
        makeProxyWrapper(xml) {
            const methods = {};
            for (const [, name, body = ''] of xml.matchAll(
                /<method name="(\w+)"\s*(?:\/>|>([\s\S]*?)<\/method>)/g)) {
                methods[name] = [...body.matchAll(/<arg type="([^"]+)" direction="in"/g)]
                    .map(m => m[1]);
            }
            const signals = [...xml.matchAll(/<signal name="(\w+)"/g)].map(m => m[1]);
            return function (_bus, _name, _path, callback, cancellable, flags) {
                shell.proxySetups.push({callback, cancellable, flags, methods, signals});
            };
        },
    },
};

// --- resource:///org/gnome/shell modules ------------------------------------------

// ui/main.js
// A notification's title says what happened, never just "TuxTalk".
export function notify(title, body) {
    if (title === 'TuxTalk')
        throw new Error(`A notification titled only "TuxTalk": ${body}`);
    shell.notifications.push([title, body]);
}

export const panel = {
    addToStatusArea(role, button) {
        shell.statusArea[role] = button;
    },
};

export const wm = {
    allowKeybinding(name, modes) {
        shell.keybindings.set(name, modes);
    },
};

// ui/popupMenu.js
export const Ornament = {NONE: 0, DOT: 1};

class PopupBaseMenuItem extends Signals {
    constructor(params = {}) {
        super();
        this.reactive = params.reactive ?? true;
        this.sensitive = true;
        this.ornament = Ornament.NONE;
    }

    setSensitive(sensitive) {
        this.sensitive = sensitive;
    }

    setOrnament(ornament) {
        this.ornament = ornament;
    }

    activate() {
        this.emit('activate', null);
    }
}

export class PopupMenuItem extends PopupBaseMenuItem {
    constructor(text, params) {
        super(params);
        this.label = {text, style: '', clutter_text: {}};
    }
}

export class PopupImageMenuItem extends PopupMenuItem {
    constructor(text, icon, params) {
        super(text, params);
        this.icon = icon;
    }
}

export class PopupSeparatorMenuItem extends PopupBaseMenuItem {}

export class PopupSwitchMenuItem extends PopupMenuItem {
    constructor(text, active) {
        super(text);
        this.state = active;
    }

    // From GNOME 47 this emits 'toggled' too, as a click does.
    setToggleState(state) {
        this.state = state;
        this.emit('toggled', state);
    }

    toggle() {
        this.setToggleState(!this.state);
    }
}

export class PopupMenuSection extends Signals {
    constructor() {
        super();
        this.items = [];
    }

    addMenuItem(item, position) {
        this.items.splice(position ?? this.items.length, 0, item);
    }

    addAction(title, callback, icon) {
        const item = icon !== undefined
            ? new PopupImageMenuItem(title, icon) : new PopupMenuItem(title);
        this.addMenuItem(item);
        item.connect('activate', (_item, event) => callback(event));
        return item;
    }

    removeAll() {
        this.items = [];
    }
}

export class PopupSubMenuMenuItem extends PopupMenuItem {
    constructor(text) {
        super(text);
        this.menu = new PopupMenuSection();
    }
}

// ui/panelMenu.js
export class Button extends Signals {
    constructor(...args) {
        super();
        this._init(...args);
    }

    _init(_alignment, nameText) {
        this.accessible_name = nameText;
        this.visible = true;
        this.children = [];
        this.menu = new PopupMenuSection();
    }

    add_child(child) {
        this.children.push(child);
    }

    show() {
        this.visible = true;
    }

    hide() {
        this.visible = false;
    }

    destroy() {
        this.destroyed = true;
    }
}

// extensions/extension.js
export class Extension {}

// --- the desktop ----------------------------------------------------------------

export class Window extends Signals {
    constructor(wmClass, id) {
        super();
        this.wmClass = wmClass;
        this.id = id;
    }

    get_wm_class() {
        return this.wmClass;
    }

    get_id() {
        return this.id;
    }

    get_frame_rect() {
        return {x: 10, y: 20, width: 800, height: 600};
    }

    get_monitor() {
        return 0;
    }
}

class Display extends Signals {
    constructor() {
        super();
        this.focus_window = null;
    }

    grab_accelerator(accelerator) {
        if (shell.taken.has(accelerator))
            return Meta.KeyBindingAction.NONE;
        shell.grabs.set(++lastId, accelerator);
        return lastId;
    }

    ungrab_accelerator(action) {
        shell.grabs.delete(action);
    }

    get_monitor_geometry() {
        return {x: 0, y: 0, width: 1920, height: 1080};
    }
}

globalThis.global = {display: new Display()};

// --- what a test drives the extension with --------------------------------------------

const EXTENSION = new URL('../../gnome-extension/talktype@ronb1964.github.io/extension.js',
    import.meta.url).href;

/** Turn the extension on (extension.js, or the copy at the file URL *url*);
 * its panel button. */
export async function enable(url = EXTENSION) {
    const {default: TuxTalkExtension} = await import(url);
    const extension = new TuxTalkExtension();
    extension.enable();
    return {extension, button: shell.statusArea.talktype};
}

/** A D-Bus error as GJS gives one. */
export function dbusError(code = 'Failed') {
    return {
        message: code,
        matches: (_domain, wanted) => wanted === code,
        toString: () => `GDBus.Error: ${code}`,
    };
}

/** A value of GetStatus's a{sv}, as GJS unpacks it. */
export const variant = value => ({deep_unpack: () => value});

// The JavaScript type GJS packs into each D-Bus type the interface uses.
const JS_TYPES = {s: 'string', b: 'boolean', i: 'number', u: 'number'};

/** Refuse a call GJS would: the wrong number of arguments, or one GJS
 * can't pack into its D-Bus type. */
function checkArgs(method, types, args) {
    if (args.length !== types.length) {
        throw new Error(`${method}Remote takes ${types.length} argument(s) ` +
            `(${types.join('')}), got ${args.length}`);
    }
    types.forEach((type, i) => {
        if (type in JS_TYPES && typeof args[i] !== JS_TYPES[type])
            throw new Error(`${method}Remote: argument ${i + 1} must be a ${type}, got ${args[i]}`);
    });
}

/**
 * TuxTalk on the bus, as the proxy the extension gets, with *methods* (name
 * -> in-args' types) as the interface declares them: each XRemote call is
 * checked as GJS checks it and kept in `calls` as [X, args], and one with a reply callback waits in
 * `waiting` until answer(X, result) or fail(X, error). signal(name, ...args)
 * emits one of its D-Bus signals.
 */
export function tuxtalk(methods) {
    const calls = [], waiting = [], subscriptions = new Map();
    const take = method => {
        const index = waiting.findIndex(w => w.method === method);
        if (index < 0)
            throw new Error(`No ${method} call is waiting for an answer`);
        return waiting.splice(index, 1)[0];
    };
    const own = {
        calls,
        waiting,
        subscriptions,
        connectSignal(name, callback) {
            subscriptions.set(++lastId, [name, callback]);
            return lastId;
        },
        disconnectSignal(id) {
            subscriptions.delete(id);
        },
        signal(name, ...args) {
            for (const [signal, callback] of [...subscriptions.values()]) {
                if (signal === name)
                    callback(proxy, ':1.42', args);
            }
        },
        answer(method, ...result) {
            take(method).callback(result, null);
        },
        fail(method, error = dbusError()) {
            take(method).callback(null, error);
        },
    };
    const proxy = new Proxy(own, {
        get(target, key) {
            if (key in target || typeof key !== 'string' || !key.endsWith('Remote'))
                return target[key];
            const method = key.slice(0, -'Remote'.length);
            if (!(method in methods))
                return undefined;
            return (...args) => {
                const callback = typeof args.at(-1) === 'function' ? args.pop() : null;
                checkArgs(method, methods[method], args);
                calls.push([method, args]);
                if (callback)
                    waiting.push({method, callback});
            };
        },
    });
    return proxy;
}

/** TuxTalk starts: it takes its bus name, and the proxy the extension asked
 * for is made. Returns that proxy (see tuxtalk). */
export function tuxtalkStarts() {
    for (const watcher of shell.watchers.filter(w => w.watching))
        watcher.appeared();
    const setup = shell.proxySetups.at(-1);
    const proxy = tuxtalk(setup.methods);
    setup.callback(proxy, null);
    return proxy;
}

/** TuxTalk quits: its bus name goes. */
export function tuxtalkQuits() {
    for (const watcher of shell.watchers.filter(w => w.watching))
        watcher.vanished();
}

/** Answer GetStatus with dictation on or off and *more* of its fields. */
export function status(proxy, running, more = {}) {
    const fields = {service_running: running, recording: false, ...more};
    proxy.answer('GetStatus', Object.fromEntries(
        Object.entries(fields).map(([key, value]) => [key, variant(value)])));
}

/** Each item's label ('---' for a separator) in *menu*. */
export function labels(menu) {
    return menu.items.map(item => item.label?.text ?? '---');
}

/** The item labelled *text* in *menu*. */
export function item(menu, text) {
    const found = menu.items.find(i => i.label?.text === text);
    if (!found)
        throw new Error(`No "${text}" in ${JSON.stringify(labels(menu))}`);
    return found;
}

/** Open the panel menu, as a click on the button does. */
export function openMenu(button) {
    button.menu.emit('open-state-changed', true);
}

/** Move the focus to *window* (null: none). */
export function focus(window) {
    global.display.focus_window = window;
    global.display.emit('notify::focus-window');
}

/** Hand *value* to the test, with what the extension logged. */
export function report(value) {
    process.stdout.write(`${JSON.stringify({value, logs: shell.logs})}\n`);
}
