// Lets Node load the GNOME extension: its gi:// and resource:/// imports
// get the stand-ins in gnome.mjs, and extension.js loads as an ES module.
// Started with `node --import` by tests/gnome_extension_js.py.
import module from 'node:module';
import {isMainThread} from 'node:worker_threads';

const STAND_INS = new URL('./gnome.mjs', import.meta.url).href;

// The libraries and GNOME Shell modules the extension imports, with what
// each gives it; an import of anything else fails, as it would in GNOME Shell.
const GI = ['GLib', 'GObject', 'Gio', 'Meta', 'Pango', 'Shell', 'St'];
const SHELL = {
    'resource:///org/gnome/shell/ui/main.js': ['notify', 'panel', 'wm'],
    'resource:///org/gnome/shell/ui/panelMenu.js': ['Button'],
    'resource:///org/gnome/shell/ui/popupMenu.js': ['Ornament', 'PopupImageMenuItem',
        'PopupMenuItem', 'PopupMenuSection', 'PopupSeparatorMenuItem',
        'PopupSubMenuMenuItem', 'PopupSwitchMenuItem'],
    'resource:///org/gnome/shell/extensions/extension.js': ['Extension'],
};

export function resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('gi://') || specifier.startsWith('resource:///'))
        return {url: specifier, shortCircuit: true};
    return nextResolve(specifier, context);
}

export function load(url, context, nextLoad) {
    if (url.startsWith('gi://') || url.startsWith('resource:///')) {
        const library = url.slice('gi://'.length);
        let source;
        if (url.startsWith('gi://') && GI.includes(library))
            source = `export {${library} as default} from '${STAND_INS}';`;
        else if (url in SHELL)
            source = `export {${SHELL[url].join(', ')}} from '${STAND_INS}';`;
        else
            throw new Error(`No such module in GNOME Shell's stand-ins: ${url}`);
        return {format: 'module', shortCircuit: true, source};
    }
    if (url.endsWith('/extension.js'))
        return nextLoad(url, {...context, format: 'module'});
    return nextLoad(url, context);
}

// registerHooks (Node 22.15 and newer) runs the hooks in this thread;
// register (20.6 and newer) loads this file again in a thread of its own.
if (module.registerHooks)
    module.registerHooks({resolve, load});
else if (isMainThread)
    module.register(import.meta.url);
