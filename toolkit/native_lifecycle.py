"""PyWebView window lifecycle, kept separate from HTTP and export work."""

import json


def run_native_window(url, load_window_state, remember_size, remember_position,
                      save_window_state, shutdown, set_native_handles,
                      export_is_running, cancel_export):
    """Run the native shell and return False only if PyWebView is unavailable."""
    try:
        import webview
        from webview.menu import Menu, MenuAction, MenuSeparator
    except ImportError:
        return False

    window = None

    def ui_action(script):
        def action():
            if window is None:
                return
            try:
                window.events.loaded.wait(10)
                window.evaluate_js(script)
            except Exception:
                pass
        return action

    menu = [
        Menu("File", [
            MenuAction("Add Files…", ui_action("pick('files')")),
            MenuAction("Add Folder…", ui_action("pick('folder')")),
            MenuSeparator(),
            MenuAction("Convert Queue", ui_action("convert()")),
        ]),
        Menu("Help", [
            MenuAction("What This Does", ui_action("document.getElementById('mHelp').classList.add('on')")),
        ]),
    ]
    state = load_window_state()
    window = webview.create_window(
        "8-bit → 10-bit Converter by Jazib Ali 360", url,
        width=state["width"], height=state["height"], x=state["x"], y=state["y"],
        min_size=(820, 620), confirm_close=False, background_color="#f4f5f7",
    )
    set_native_handles(window, webview)

    def install_native_drop_bridge():
        """Pass real OS drag-and-drop paths instead of browser-upload copies.

        Web views intentionally hide absolute paths from page JavaScript.
        PyWebView's DOM bridge restores them on its supported native backends,
        keeping "next to each source" destinations truthful on macOS and
        Windows. The Add button remains the reliable fallback for older hosts.
        """
        try:
            from webview.dom import DOMEventHandler
            queue = window.dom.get_element("#queue")
            if queue is None:
                return

            def receive_drop(event):
                transfer = event.get("dataTransfer") or {}
                paths = [f.get("pywebviewFullPath") for f in transfer.get("files") or []
                         if f.get("pywebviewFullPath")]
                if paths:
                    window.evaluate_js(
                        "window.addNativeDroppedFiles(" + json.dumps(paths) + ");"
                    )

            # This enables PyWebView's native path bridge. The page's normal
            # drop listener skips browser upload when running in the app.
            queue.on("drop", DOMEventHandler(receive_drop, prevent_default=True))
        except Exception:
            # Add remains a reliable native-picker fallback if the installed
            # PyWebView runtime lacks DOM drop support.
            pass

    window.events.loaded += install_native_drop_bridge

    def confirm_export_aware_close():
        if not export_is_running():
            return True
        cancel_and_quit = window.create_confirmation_dialog(
            "Export in progress",
            "Cancel the current export and quit?\n\nChoose Cancel to keep exporting.",
        )
        if not cancel_and_quit:
            return False
        cancel_export()
        return True

    window.events.closing += confirm_export_aware_close
    window.events.resized += remember_size
    window.events.moved += remember_position
    window.events.closed += lambda: (save_window_state(), __import__("threading").Thread(target=shutdown, daemon=True).start())
    webview.start(menu=menu)
    shutdown()
    return True
