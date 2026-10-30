// Lets Node load the GNOME extension: its gi:// and resource:/// imports
// get the stand-ins in gnome.mjs, and extension.js loads as an ES module.
// Started with `node --import` by tests/gnome_extension_js.py.
import module from 'node:module';
import {isMainThread} from 'node:worker_threads';

const STAND_INS = new URL('./gnome.mjs', import.meta.url).href;

export function resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('gi://') || specifier.startsWith('resource:///'))
        return {url: specifier, shortCircuit: true};
    return nextResolve(specifier, context);
}

// gi://GLib is gnome.mjs's GLib; every resource:/// module is all of
// gnome.mjs, which holds Main's, PanelMenu's, PopupMenu's and Extension's
// names apart from one another.
export function load(url, context, nextLoad) {
    if (url.startsWith('gi://')) {
        const name = url.slice('gi://'.length);
        return {format: 'module', shortCircuit: true,
            source: `export {${name} as default} from '${STAND_INS}';`};
    }
    if (url.startsWith('resource:///'))
        return {format: 'module', shortCircuit: true, source: `export * from '${STAND_INS}';`};
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
