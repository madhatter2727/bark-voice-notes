import AppKit
import AVFoundation
import Darwin
import UniformTypeIdentifiers
import WebKit

private let appOrigin = "http://127.0.0.1:8099"
private let statusURL = URL(string: "http://127.0.0.1:8099/api/status")!
private let homeURL = URL(string: "http://127.0.0.1:8099/")!
private let quitURL = URL(string: "http://127.0.0.1:8099/api/quit")!
private let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/BarkVoiceNotes-server.log")
private let pageBackground = NSColor(srgbRed: 22.0 / 255.0, green: 19.0 / 255.0, blue: 15.0 / 255.0, alpha: 1)

// server.py runs this binary as BARK_TRASH_HELPER: `BarkVoiceNotes --trash <folder>`.
private func exitIfTrashHelper() {
    let args = Array(CommandLine.arguments.dropFirst())
    guard args.count == 2, args[0] == "--trash" else { return }
    do {
        var resulting: NSURL?
        try FileManager.default.trashItem(at: URL(fileURLWithPath: args[1]), resultingItemURL: &resulting)
        if let path = resulting?.path { print(path) }
    } catch {
        let line = error.localizedDescription + "\n"
        try? FileHandle.standardError.write(contentsOf: Data(line.utf8))
        exit(1)
    }
    exit(0)
}

private func escapeHTML(_ text: String) -> String {
    text
        .replacingOccurrences(of: "&", with: "&amp;")
        .replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var ownedProcess: Process?
    private var logHandles: [FileHandle] = []
    private var explainedMicDenial = false
    private var launchDeadline = Date.distantFuture
    private var pageReady = false
    private var launchFailed = false
    private var quitStarted = false
    private var didReplyToTerminate = false
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 1.5
        config.timeoutIntervalForResource = 4
        config.waitsForConnectivity = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }()

    func applicationWillFinishLaunching(_ notification: Notification) {
        makeWindow()
        makeMenu()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(webView)
        NSApp.activate()
        showStartingPage()
        beginServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // Models stay loaded in the server process, so quit stops that process
    // even when this app attached to one it did not start.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if !quitStarted {
            quitStarted = true
            postQuitThenStopProcess()
        }
        return .terminateLater
    }

    private func makeWindow() {
        let webConfig = WKWebViewConfiguration()
        webConfig.applicationNameForUserAgent = "BarkVoiceNotesApp/1.0"
        webConfig.mediaTypesRequiringUserActionForPlayback = []
        let preferences = webConfig.preferences
        preferences.javaScriptCanOpenWindowsAutomatically = true
        webConfig.preferences = preferences

        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1180, height: 780), configuration: webConfig)
        if #available(macOS 13.3, *) {
            web.isInspectable = true
        }
        web.allowsBackForwardNavigationGestures = false
        web.underPageBackgroundColor = pageBackground
        web.uiDelegate = self
        web.navigationDelegate = self
        webView = web

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Bark Voice Notes"
        window.minSize = NSSize(width: 820, height: 560)
        window.backgroundColor = pageBackground
        window.isReleasedWhenClosed = false
        window.contentView = web
        window.initialFirstResponder = web
        if !window.setFrameAutosaveName("BarkMain") {
            window.center()
        }
        self.window = window
    }

    private func makeMenu() {
        let main = NSMenu()

        let appMenu = NSMenu(title: "Bark Voice Notes")
        appMenu.addItem(menuItem("About Bark Voice Notes", #selector(NSApplication.orderFrontStandardAboutPanel(_:))))
        appMenu.addItem(.separator())
        appMenu.addItem(menuItem("Hide", #selector(NSApplication.hide(_:)), key: "h"))
        appMenu.addItem(menuItem(
            "Hide Others",
            #selector(NSApplication.hideOtherApplications(_:)),
            key: "h",
            modifiers: [.command, .option]
        ))
        appMenu.addItem(menuItem("Show All", #selector(NSApplication.unhideAllApplications(_:))))
        appMenu.addItem(.separator())
        appMenu.addItem(menuItem("Quit", #selector(NSApplication.terminate(_:)), key: "q"))
        main.addItem(submenuItem(appMenu))

        let edit = NSMenu(title: "Edit")
        edit.addItem(menuItem("Undo", Selector(("undo:")), key: "z"))
        edit.addItem(menuItem("Redo", Selector(("redo:")), key: "z", modifiers: [.command, .shift]))
        edit.addItem(.separator())
        edit.addItem(menuItem("Cut", #selector(NSText.cut(_:)), key: "x"))
        edit.addItem(menuItem("Copy", #selector(NSText.copy(_:)), key: "c"))
        edit.addItem(menuItem("Paste", #selector(NSText.paste(_:)), key: "v"))
        edit.addItem(.separator())
        edit.addItem(menuItem("Select All", #selector(NSText.selectAll(_:)), key: "a"))
        main.addItem(submenuItem(edit))

        let view = NSMenu(title: "View")
        let reload = menuItem("Reload", #selector(AppDelegate.reloadPage(_:)), key: "r")
        reload.target = self
        view.addItem(reload)
        main.addItem(submenuItem(view))

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(menuItem("Minimize", #selector(NSWindow.performMiniaturize(_:)), key: "m"))
        windowMenu.addItem(menuItem("Zoom", #selector(NSWindow.performZoom(_:))))
        main.addItem(submenuItem(windowMenu))

        NSApp.mainMenu = main
    }

    private func menuItem(
        _ title: String,
        _ action: Selector,
        key: String = "",
        modifiers: NSEvent.ModifierFlags? = nil
    ) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        if let modifiers {
            item.keyEquivalentModifierMask = modifiers
        }
        return item
    }

    private func submenuItem(_ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem()
        item.submenu = menu
        return item
    }

    @objc private func reloadPage(_ sender: Any?) {
        webView.reload()
    }

    private func showStartingPage() {
        let html = """
        <!DOCTYPE html>
        <html><head><meta charset="utf-8"><style>
        html, body { margin: 0; height: 100%; background: #16130f; color: #f6f1e8;
          font: 18px/1.4 -apple-system, BlinkMacSystemFont, sans-serif;
          display: flex; align-items: center; justify-content: center; }
        </style></head>
        <body>Starting Bark Voice Notes…</body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func showFailure(_ reason: String) {
        launchFailed = true
        let paragraphs = [
            reason,
            "Log: \(logURL.path)",
            "If the Python environment is missing, run setup.sh in the bark-voice-notes project, then open the app again.",
        ]
        let body = paragraphs.map { "<p>\(escapeHTML($0))</p>" }.joined()
        let html = """
        <!DOCTYPE html>
        <html><head><meta charset="utf-8"><style>
        html, body { margin: 0; height: 100%; background: #16130f; color: #f6f1e8;
          font: 16px/1.45 -apple-system, BlinkMacSystemFont, sans-serif; }
        main { max-width: 36rem; margin: 0 auto; padding: 18vh 24px 24px; }
        h1 { font-size: 1.35rem; font-weight: 650; margin: 0 0 12px; }
        p { margin: 0 0 10px; color: #a89a88; }
        </style></head>
        <body><main><h1>Bark Voice Notes did not start</h1>\(body)</main></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func pythonExecutable() -> String {
        let override = ProcessInfo.processInfo.environment["BARK_PYTHON"]?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let raw = override.isEmpty ? "~/Projects/bark-voice-notes/.venv/bin/python" : override
        return NSString(string: raw).expandingTildeInPath
    }

    private func beginServer() {
        fetchStatus(timeout: 1) { ok in
            if self.quitStarted { return }
            if ok {
                self.loadApp()
                return
            }
            self.launchOwnedServer()
        }
    }

    private func launchOwnedServer() {
        let python = pythonExecutable()
        guard FileManager.default.isExecutableFile(atPath: python) else {
            showFailure("Python was not found at \(python).")
            return
        }
        guard let script = Bundle.main.resourceURL?.appendingPathComponent("server/server.py"),
              FileManager.default.isReadableFile(atPath: script.path),
              !directoryExists(script.path) else {
            let place = Bundle.main.resourceURL?.appendingPathComponent("server/server.py").path ?? "the app resources"
            showFailure("server.py was not found at \(place). Install again with mac/install-app.sh.")
            return
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [script.path]
        process.currentDirectoryURL = script.deletingLastPathComponent()
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        if let executable = Bundle.main.executablePath {
            env["BARK_TRASH_HELPER"] = executable
        }
        process.environment = env
        process.standardInput = FileHandle.nullDevice

        do {
            // One handle: Process dups the fd, so stdout and stderr share the offset.
            let log = try appendLogHandle()
            process.standardOutput = log
            process.standardError = log
            try process.run()
        } catch {
            showFailure("The server process did not start (\(error.localizedDescription)).")
            return
        }

        ownedProcess = process
        launchDeadline = Date().addingTimeInterval(40)
        pollUntilReady()
    }

    private func directoryExists(_ path: String) -> Bool {
        var isDir: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &isDir) && isDir.boolValue
    }

    private func appendLogHandle() throws -> FileHandle {
        try FileManager.default.createDirectory(
            at: logURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        if !FileManager.default.fileExists(atPath: logURL.path) {
            guard FileManager.default.createFile(atPath: logURL.path, contents: nil) else {
                throw CocoaError(.fileWriteUnknown)
            }
        }
        let handle = try FileHandle(forWritingTo: logURL)
        _ = handle.seekToEndOfFile()
        logHandles.append(handle)
        return handle
    }

    private func pollUntilReady() {
        if quitStarted || pageReady || launchFailed { return }
        if let ownedProcess, !ownedProcess.isRunning {
            showFailure("The server stopped before it was ready (exit \(ownedProcess.terminationStatus)).")
            return
        }
        if Date() >= launchDeadline {
            showFailure("The server did not answer within 40 seconds.")
            return
        }
        fetchStatus(timeout: 0.4) { ok in
            if self.quitStarted || self.pageReady || self.launchFailed { return }
            if ok {
                self.loadApp()
                return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                MainActor.assumeIsolated {
                    self.pollUntilReady()
                }
            }
        }
    }

    private func loadApp() {
        pageReady = true
        webView.load(URLRequest(url: homeURL))
    }

    private func fetchStatus(timeout: TimeInterval, done: @escaping @MainActor (Bool) -> Void) {
        var request = URLRequest(url: statusURL, timeoutInterval: timeout)
        request.httpMethod = "GET"
        request.cachePolicy = .reloadIgnoringLocalCacheData
        session.dataTask(with: request) { _, response, error in
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            let ok = error == nil && (200 ... 299).contains(code)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    done(ok)
                }
            }
        }.resume()
    }

    private func postQuitThenStopProcess() {
        var request = URLRequest(url: quitURL, timeoutInterval: 1.5)
        request.httpMethod = "POST"
        request.setValue(appOrigin, forHTTPHeaderField: "Origin")
        session.dataTask(with: request) { _, _, _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.stopOwnedProcessThenReply()
                }
            }
        }.resume()
    }

    private func stopOwnedProcessThenReply() {
        guard let ownedProcess, ownedProcess.isRunning else {
            replyTerminate()
            return
        }
        ownedProcess.terminate()
        scheduleOwnedExit(ownedProcess, until: Date().addingTimeInterval(3))
    }

    private func scheduleOwnedExit(_ process: Process, until deadline: Date) {
        if !process.isRunning || Date() >= deadline {
            if process.isRunning {
                _ = Darwin.kill(process.processIdentifier, SIGKILL)
            }
            replyTerminate()
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            MainActor.assumeIsolated {
                self.scheduleOwnedExit(process, until: deadline)
            }
        }
    }

    private func replyTerminate() {
        if didReplyToTerminate { return }
        didReplyToTerminate = true
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    func webView(
        _ webView: WKWebView,
        requestMediaCapturePermissionFor origin: WKSecurityOrigin,
        initiatedByFrame frame: WKFrameInfo,
        type: WKMediaCaptureType,
        decisionHandler: @escaping (WKPermissionDecision) -> Void
    ) {
        guard origin.host == "127.0.0.1" else {
            decisionHandler(.deny)
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            decisionHandler(.grant)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                let decision: WKPermissionDecision = granted ? .grant : .deny
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        decisionHandler(decision)
                    }
                }
            }
        default:
            decisionHandler(.deny)
            explainMicDenial()
        }
    }

    private func explainMicDenial() {
        if explainedMicDenial { return }
        explainedMicDenial = true
        let alert = NSAlert()
        alert.messageText = "Turn on microphone access"
        alert.informativeText = "System Settings → Privacy & Security → Microphone → turn on Bark Voice Notes."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window)
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping () -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window) { _ in
            completionHandler()
        }
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (Bool) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        if message.contains("Trash") {
            alert.addButton(withTitle: "Move to Trash")
        } else {
            alert.addButton(withTitle: "OK")
        }
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { response in
            completionHandler(response == .alertFirstButtonReturn)
        }
    }

    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowedContentTypes = [.audio]
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }
        let scheme = url.scheme?.lowercased() ?? ""
        if scheme == "about" || scheme == "data" {
            decisionHandler(.allow)
            return
        }
        // A file dropped on the window, outside the page's drop zone, navigates here.
        if scheme == "file" {
            decisionHandler(.cancel)
            return
        }
        if scheme == "http" || scheme == "https" {
            if scheme == "http", url.host == "127.0.0.1", url.port == 8099 {
                decisionHandler(.allow)
                return
            }
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.cancel)
    }
}

exitIfTrashHelper()
MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.setActivationPolicy(.regular)
    app.delegate = delegate
    withExtendedLifetime(delegate) {
        app.run()
    }
}
