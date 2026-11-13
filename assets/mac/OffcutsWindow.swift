// The Offcuts window: a plain Mac app around the local Offcuts page, so it opens in its own
// window with its own Dock icon instead of a browser tab. scripts/make_app.sh builds it into the app.
// It starts the background server if it isn't running (Resources/start-server.sh) and leaves it
// running when the window closes, so anything being made carries on. Outside links (Posting Desk)
// open in the browser.
import Cocoa
import WebKit

let port = ProcessInfo.processInfo.environment["DOKK_PORT"] ?? "8765"
let home = URL(string: "http://127.0.0.1:\(port)/")!
let appName = (Bundle.main.infoDictionary?["CFBundleName"] as? String) ?? "Offcuts"

func isLocal(_ url: URL) -> Bool {
    if url.scheme == "about" || url.scheme == "blob" || url.scheme == "data" { return true }
    return (url.host == "127.0.0.1" || url.host == "localhost") && url.port == home.port
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        if #available(macOS 12.3, *) { config.preferences.isElementFullscreenEnabled = true }
        config.mediaTypesRequiringUserActionForPlayback = []
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.setValue(false, forKey: "drawsBackground")  // no white flash before the page's own dark background

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.minSize = NSSize(width: 720, height: 500)
        window.backgroundColor = NSColor(red: 0.08, green: 0.08, blue: 0.09, alpha: 1)
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("OffcutsMain")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        web.loadHTMLString(waitingPage("Starting \(appName)…"), baseURL: nil)
        DispatchQueue.global().async { self.startServer() }
    }

    // the server: use the running one, or start it and wait (it can take a minute the first time)
    func serverUp() -> Bool {
        var req = URLRequest(url: home.appendingPathComponent("api/state"), timeoutInterval: 2)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        let done = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            ok = (resp as? HTTPURLResponse)?.statusCode == 200; done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 3)
        return ok
    }

    func startServer() {
        if !serverUp() {
            if let script = Bundle.main.url(forResource: "start-server", withExtension: "sh") {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/bin/bash")
                p.arguments = [script.path]
                try? p.run()
            }
            var up = false
            for _ in 0..<180 {  // up to 90 seconds
                Thread.sleep(forTimeInterval: 0.5)
                if serverUp() { up = true; break }
            }
            if !up {
                DispatchQueue.main.async {
                    self.web.loadHTMLString(self.waitingPage("\(appName) didn't start. Close this window and open it again; if it still won't start, the details are in Working files/app-server.log.", spin: false), baseURL: nil)
                }
                return
            }
        }
        DispatchQueue.main.async { self.web.load(URLRequest(url: home)) }
    }

    func waitingPage(_ text: String, spin: Bool = true) -> String {
        """
        <html><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
        background:#141416;color:#f1efe9;font:15px -apple-system,system-ui,sans-serif">
        <div style="display:flex;gap:12px;align-items:center;max-width:460px;line-height:1.5">
        \(spin ? "<div style='width:16px;height:16px;border:2px solid #555;border-top-color:#5fcf95;border-radius:50%;animation:s 1s linear infinite'></div>" : "")
        <div>\(text)</div></div><style>@keyframes s{to{transform:rotate(360deg)}}</style></body></html>
        """
    }

    // links: Offcuts' own pages stay in the window, everything else opens in the browser
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, !isLocal(url) {
            if action.targetFrame?.isMainFrame ?? true { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith config: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {  // target="_blank"
            if isLocal(url) { webView.load(URLRequest(url: url)) } else { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    // the page's own alerts, confirms and prompts (Cancel project asks first)
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = message; a.addButton(withTitle: "OK")
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = message
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = prompt
        let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24)); f.stringValue = defaultText ?? ""
        a.accessoryView = f
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn ? f.stringValue : nil) }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let p = NSOpenPanel(); p.allowsMultipleSelection = parameters.allowsMultipleSelection
        p.beginSheetModal(for: window) { r in completionHandler(r == .OK ? p.urls : nil) }
    }

    // back to the window after the server stopped (a restart, a crash): start it again
    var checking = false
    func applicationDidBecomeActive(_ note: Notification) {
        guard web != nil, !checking, web.url.map(isLocal) ?? false, web.url?.scheme == "http" else { return }
        checking = true
        DispatchQueue.global().async {
            if !self.serverUp() {
                DispatchQueue.main.async { self.web.loadHTMLString(self.waitingPage("Starting \(appName) again…"), baseURL: nil) }
                self.startServer()
            }
            DispatchQueue.main.async { self.checking = false }
        }
    }

    // reopen from the Dock after closing the window: the app quits with its window, so this starts afresh
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    @objc func reloadPage(_ sender: Any?) { web.reload() }
    @objc func goHome(_ sender: Any?) { web.load(URLRequest(url: home)) }
    @objc func goBack(_ sender: Any?) { web.goBack() }

    func buildMenu() {
        let main = NSMenu()
        func add(_ title: String, _ items: [NSMenuItem]) {
            let top = NSMenuItem(); let m = NSMenu(title: title)
            items.forEach { m.addItem($0) }; top.submenu = m; main.addItem(top)
        }
        func item(_ t: String, _ a: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k); i.keyEquivalentModifierMask = mods; return i
        }
        add(appName, [item("Hide \(appName)", #selector(NSApplication.hide(_:)), "h"),
                      item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
                      item("Show All", #selector(NSApplication.unhideAllApplications(_:)), ""),
                      .separator(),
                      item("Quit \(appName)", #selector(NSApplication.terminate(_:)), "q")])
        add("Edit", [item("Undo", Selector(("undo:")), "z"), item("Redo", Selector(("redo:")), "Z"), .separator(),
                     item("Cut", #selector(NSText.cut(_:)), "x"), item("Copy", #selector(NSText.copy(_:)), "c"),
                     item("Paste", #selector(NSText.paste(_:)), "v"), item("Select All", #selector(NSText.selectAll(_:)), "a")])
        let reload = item("Reload", #selector(reloadPage(_:)), "r"); reload.target = self
        let homeItem = item("Episodes", #selector(goHome(_:)), "1"); homeItem.target = self
        let back = item("Back", #selector(goBack(_:)), "["); back.target = self
        add("View", [reload, homeItem, back, .separator(),
                     item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])])
        let win = [item("Minimise", #selector(NSWindow.performMiniaturize(_:)), "m"),
                   item("Zoom", #selector(NSWindow.performZoom(_:)), ""),
                   item("Close", #selector(NSWindow.performClose(_:)), "w")]
        add("Window", win)
        NSApp.mainMenu = main
        NSApp.windowsMenu = main.items.last?.submenu
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
