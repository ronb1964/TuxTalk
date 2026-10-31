// Stand-ins for what KWin gives TuxTalk's focus script (kwin_focus.py), and
// the controls a test drives it with: windows, focus moving between them,
// drags and resizes. Each callDBus the script makes is kept in dbusCalls.
// Only as much of KWin's scripting API as the script uses, as Plasma 6 has it.
// gnome_extension_js.run_kwin_script puts the script and the test after this.

/** connect, disconnect and emit, as a KWin signal. */
class Signal {
    constructor() {
        this.handlers = [];
    }

    connect(handler) {
        this.handlers.push(handler);
    }

    disconnect(handler) {
        const at = this.handlers.indexOf(handler);
        if (at < 0) {
            throw new Error('disconnect of a handler that was never connected');
        }
        this.handlers.splice(at, 1);
    }

    emit(...args) {
        for (const handler of [...this.handlers]) {
            handler(...args);
        }
    }
}

/** [service, path, interface, method, ...args] for each call. */
const dbusCalls = [];

function callDBus(...call) {
    dbusCalls.push(call);
}

/** A window of *resourceClass* at x, y, width, height on a 1920x1080 screen. */
function makeWindow(resourceClass, internalId, x, y, width, height) {
    return {
        resourceClass, internalId,
        frameGeometry: {x, y, width, height},
        output: {name: 'DP-1', geometry: {x: 0, y: 0, width: 1920, height: 1080}},
        move: false,
        resize: false,
        frameGeometryChanged: new Signal(),
        interactiveMoveResizeFinished: new Signal(),
    };
}

const workspace = {activeWindow: null, windowActivated: new Signal()};

/** The user clicks into *window*. */
function activate(window) {
    workspace.activeWindow = window;
    workspace.windowActivated.emit(window);
}

/** KWin loads the script, with whatever window is active now. */
function load() {
    (0, eval)(KWIN_SCRIPT);
}

/** Hand *value* back to the test. */
function report(value) {
    console.log(JSON.stringify(value));
}
