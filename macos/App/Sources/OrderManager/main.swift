#if canImport(AppKit)
import AppKit
import ServiceManagement
import Darwin
import CFileLock

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var item: NSStatusItem!
    let stateItem = NSMenuItem(title: "Checking status", action: nil, keyEquivalent: "")
    var startupItem: NSMenuItem!
    var extensionItem: NSMenuItem!
    var actions: [NSMenuItem] = []
    let helper: HelperRunning = HelperClient()
    var progress = ProgressState()
    var window: NSWindow!
    var step: NSTextField!
    var elapsed: NSTextField!
    var activity: NSTextView!
    var timer: Timer?
    var started = Date()
    var polling = false
    var initialized = false
    var autoStart = true
    var replacement = false
    var extensionReminder = false
    var lastStatus: [String: Any] = [:]
    let target = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications/Order Manager.app")
    let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/OrderManager")

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard FileManager.default.homeDirectoryForCurrentUser.path != "/var/root" else { alert("Run Order Manager as your normal Mac user."); NSApp.terminate(nil); return }
        guard installIfNeeded() else { NSApp.terminate(nil); return }
        if NSRunningApplication.runningApplications(withBundleIdentifier: "com.penner42.OrderManager").contains(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier && $0.bundleURL?.standardizedFileURL == target.standardizedFileURL }) {
            NSApp.terminate(nil); return
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "shippingbox", accessibilityDescription: "Order Manager")
        let menu = NSMenu(); menu.addItem(stateItem)
        add(menu, "Open Order Manager", #selector(openApp)); add(menu, "Show Setup Progress", #selector(showProgress))
        menu.addItem(.separator())
        add(menu, "Start", #selector(start), operation: true); add(menu, "Stop", #selector(stop), operation: true)
        add(menu, "Restart", #selector(restart), operation: true); add(menu, "Update", #selector(update), operation: true)
        add(menu, "Install Docker Desktop", #selector(installDocker), operation: true)
        extensionItem = add(menu, "Browser Extensions", #selector(extensions), operation: true)
        add(menu, "View Logs", #selector(logs)); add(menu, "Recovery Instructions", #selector(recovery), operation: true)
        add(menu, "Resume After Manual Restore", #selector(recover), operation: true)
        startupItem = add(menu, "Launch at Login", #selector(toggleStartup), operation: true)
        menu.addItem(.separator()); add(menu, "Uninstall", #selector(uninstall), operation: true)
        add(menu, "Quit", #selector(quit), operation: true)
        menu.autoenablesItems = false; stateItem.isEnabled = false; item.menu = menu
        makeProgressWindow()
        poll()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            if self.progress.busy { self.elapsed.stringValue = "Elapsed: \(Int(Date().timeIntervalSince(self.started))) seconds" }
            if Int(Date().timeIntervalSince1970) % 15 == 0 { self.poll() }
        }
    }
    @discardableResult func add(_ menu: NSMenu, _ title: String, _ selector: Selector, operation: Bool = false) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: ""); entry.target = self; menu.addItem(entry)
        if operation { actions.append(entry) }; return entry
    }
    func installIfNeeded() -> Bool {
        if Bundle.main.bundleURL.standardizedFileURL == target.standardizedFileURL { return true }
        guard confirm("Install Order Manager?", "Install for your account at \(target.path). Docker Desktop and application setup will follow.", button: "Install and Open") else { return false }
        if NSRunningApplication.runningApplications(withBundleIdentifier: "com.penner42.OrderManager").contains(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier && $0.bundleURL?.standardizedFileURL == target.standardizedFileURL }) {
            alert("Quit the installed Order Manager launcher before reinstalling. Services can stay running."); return false
        }
        let fileManager = FileManager.default
        let stageRoot = target.deletingLastPathComponent().appendingPathComponent(".OrderManager-installing", isDirectory: true)
        let stage = stageRoot.appendingPathComponent("Order Manager.app", isDirectory: true)
        do {
            let config = root.appendingPathComponent("config")
            try fileManager.createDirectory(at: config, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let lock = Darwin.open(config.appendingPathComponent("operation.lock").path, O_CREAT | O_RDWR, 0o600)
            guard lock >= 0 else { throw NSError(domain: "Installer", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not open the installation lock."]) }
            defer { Darwin.close(lock) }
            guard order_manager_try_lock(lock) == 0 else { throw NSError(domain: "Installer", code: 1, userInfo: [NSLocalizedDescriptionKey: "Another operation is running. Wait for it to finish before reinstalling."]) }
            try fileManager.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try fileManager.createDirectory(at: stageRoot, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            if fileManager.fileExists(atPath: stage.path) { try fileManager.removeItem(at: stage) }
            try fileManager.copyItem(at: Bundle.main.bundleURL, to: stage)
            let verify = Process(); verify.executableURL = URL(fileURLWithPath: "/usr/bin/codesign"); verify.arguments = ["--verify", "--deep", "--strict", stage.path]
            try verify.run(); verify.waitUntilExit(); guard verify.terminationStatus == 0 else { throw NSError(domain: "Installer", code: 1, userInfo: [NSLocalizedDescriptionKey: "Application signature verification failed."]) }
            if fileManager.fileExists(atPath: target.path) {
                let old = target.appendingPathExtension("previous")
                if fileManager.fileExists(atPath: old.path) { try fileManager.removeItem(at: old) }
                try fileManager.moveItem(at: target, to: old)
                do { try fileManager.moveItem(at: stage, to: target) }
                catch { try fileManager.moveItem(at: old, to: target); throw error }
            } else { try fileManager.moveItem(at: stage, to: target) }
            // Reinstall repairs only the launcher journal. Never touch migration/service state.
            for name in ["launcher-replacement.json", "launcher-replacement-error.txt"] {
                let journal = config.appendingPathComponent(name)
                if fileManager.fileExists(atPath: journal.path) { try fileManager.removeItem(at: journal) }
            }
            NSWorkspace.shared.openApplication(at: target, configuration: NSWorkspace.OpenConfiguration(), completionHandler: nil)
        } catch { alert("Installation could not finish: \(error.localizedDescription)") }
        return false
    }
    func makeProgressWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 700, height: 420), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Order Manager Setup"; window.isReleasedWhenClosed = false; window.center(); window.delegate = self
        let content = window.contentView!
        step = NSTextField(labelWithString: progress.step); step.frame = NSRect(x: 20,y: 370,width: 660,height: 30); step.autoresizingMask = [.width, .minYMargin]; content.addSubview(step)
        elapsed = NSTextField(labelWithString: ""); elapsed.frame = NSRect(x: 20,y: 340,width: 660,height: 25); elapsed.autoresizingMask = [.width,.minYMargin]; content.addSubview(elapsed)
        let scroll = NSScrollView(frame: NSRect(x: 20,y: 20,width: 660,height: 310)); scroll.autoresizingMask = [.width,.height]; scroll.hasVerticalScroller = true
        activity = NSTextView(frame: scroll.bounds); activity.isEditable = false; activity.isSelectable = true; activity.autoresizingMask = [.width]; activity.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        scroll.documentView = activity; content.addSubview(scroll)
    }
    func refreshProgress() {
        step.stringValue = progress.step
        activity.string = progress.activity + (progress.error.map { "\n\($0)\n\nView Logs or choose Start to retry. If recovery is required, follow Recovery Instructions first." } ?? "")
        activity.scrollToEndOfDocument(nil)
    }
    func run(_ args: [String], done: ((Bool) -> Void)? = nil) {
        guard !progress.busy && !polling else { return }
        progress.begin(); started = Date(); elapsed.stringValue = "Elapsed: 0 seconds"; replacement = false; actions.forEach { $0.isEnabled = false }; refreshProgress(); showProgress()
        helper.run(args, event: { event in
            self.progress.receive(event)
            if event.type == "open", let path = event.data as? String { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
            if event.type == "replacement" { self.replacement = event.data as? Bool ?? false }
            self.refreshProgress()
        }, completion: { code in
            self.progress.finish(code); self.refreshProgress(); self.actions.forEach { $0.isEnabled = true }
            let success = self.progress.error == nil
            if self.replacement && success { NSApp.terminate(nil); return }
            done?(success)
            if success && ["start", "update", "restart"].contains(args[0]) { self.run(["stage-replacement", String(ProcessInfo.processInfo.processIdentifier)]) }
        })
    }
    func poll() {
        guard !progress.busy && !polling else { return }; polling = true
        actions.forEach { $0.isEnabled = false }
        helper.run(["status"], event: { event in
            if event.type == "state", let state = event.data as? [String: Any] { self.lastStatus = state }
            if event.type == "error", let message = event.data as? String { self.stateItem.title = message }
        }, completion: { code in
            self.polling = false
            self.actions.forEach { $0.isEnabled = true }
            guard code == 0, !self.lastStatus.isEmpty else { return }
            self.stateItem.title = self.lastStatus["status"] as? String ?? "Unknown"
            self.autoStart = self.lastStatus["autoStart"] as? Bool ?? true
            self.extensionReminder = self.lastStatus["extensionReminder"] as? Bool ?? false
            self.extensionItem.title = self.extensionReminder ? "Browser Extensions — Update Reminder" : "Browser Extensions"
            self.item.button?.toolTip = "Order Manager: \(self.stateItem.title)" + (self.extensionReminder ? " — browser extensions need updating" : "")
            self.refreshStartup()
            guard !self.initialized else { return }; self.initialized = true
            if self.autoStart { self.configureStartup(true) }
            if self.lastStatus["replacementPending"] as? Bool == true {
                if self.lastStatus["replacementFailed"] as? Bool == true {
                    self.alert("Launcher replacement could not finish. View Logs, then choose Start to retry or reinstall the Mac installer. Your services and data are retained.")
                } else { self.run(["stage-replacement", String(ProcessInfo.processInfo.processIdentifier)]); return }
            }
            if self.autoStart || self.lastStatus["installed"] as? Bool == false { self.start() }
        })
    }
    func refreshStartup() {
        let status = SMAppService.mainApp.status
        startupItem.state = status == .enabled ? .on : .off
        startupItem.title = status == .requiresApproval ? "Launch at Login — Approval Required" : "Launch at Login"
    }
    func configureStartup(_ enabled: Bool) {
        do {
            if enabled && SMAppService.mainApp.status != .enabled && SMAppService.mainApp.status != .requiresApproval { try SMAppService.mainApp.register() }
            if !enabled && SMAppService.mainApp.status != .notRegistered { try SMAppService.mainApp.unregister() }
            refreshStartup()
            if SMAppService.mainApp.status == .requiresApproval { alert("Enable Order Manager in System Settings → General → Login Items to allow startup."); SMAppService.openSystemSettingsLoginItems() }
        } catch { alert("Could not change launch at login: \(error.localizedDescription)"); refreshStartup() }
    }
    @objc func toggleStartup() {
        let enabled = SMAppService.mainApp.status == .notRegistered || SMAppService.mainApp.status == .notFound
        configureStartup(enabled)
        let desired = enabled && (SMAppService.mainApp.status == .enabled || SMAppService.mainApp.status == .requiresApproval)
        run(["autostart", desired ? "true" : "false"])
    }
    @objc func start() {
        let installed = ["/Applications/Docker.app", FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications/Docker.app").path].contains { FileManager.default.fileExists(atPath: $0) }
        if !installed { installDocker() } else { run(["start"]) }
    }
    @objc func stop() { run(["stop"]) }
    @objc func restart() { run(["restart"]) }
    @objc func update() { run(["update"]) }
    @objc func installDocker() {
        guard confirm("Install Docker Desktop?", "Download and open Docker's official Apple Silicon installer. You will install Docker, launch it, and review its license and any permission prompts. Then choose Start here.", button: "Download Docker") else { return }
        run(["install-docker"])
    }
    @objc func recovery() { run(["recovery"]) }
    @objc func recover() {
        if confirm("Resume after restoring the database?", "Continue only after all restore commands in Recovery Instructions succeeded. Have you restored the backup and stopped application writers?", button: "Restore Completed") { run(["recover", "--confirmed"]) }
    }
    @objc func openApp() { NSWorkspace.shared.open(URL(string: "http://localhost:5173")!) }
    @objc func showProgress() { window?.makeKeyAndOrderFront(nil); window?.deminiaturize(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func logs() { NSWorkspace.shared.open(root.appendingPathComponent("logs")) }
    @objc func extensions() {
        NSWorkspace.shared.open(URL(string: "http://localhost:5173/settings/extension")!)
        NSWorkspace.shared.open(root.appendingPathComponent("extensions"))
        if confirm("Browser Extensions" + (extensionReminder ? " — Update Reminder" : ""), "Chrome: enable Developer mode at chrome://extensions and Load Unpacked from:\n\(root.appendingPathComponent("extensions/chrome").path)\nAfter updates, click Reload.\n\nFirefox: install the signed XPI from Settings → Browser extension once; later updates use Firefox's updater.\n\nSet the server URL to http://localhost:5173 and sign in. Have you installed or updated every browser profile you use?", button: "Acknowledge Updated") { run(["acknowledge-extensions"]) }
    }
    @objc func uninstall() {
        guard confirm("Uninstall Order Manager?", "Stop this app's containers, disable launch at login, and move the launcher to Trash. All data, configuration, backups, extensions, and Docker Desktop will be retained.", button: "Uninstall") else { return }
        run(["stop"]) { success in
            guard success else { return }
            do {
                if SMAppService.mainApp.status != .notRegistered { try SMAppService.mainApp.unregister() }
                NSWorkspace.shared.recycle([self.target]) { _, error in
                    DispatchQueue.main.async { if let error { self.alert("Could not remove the app: \(error.localizedDescription)") } else { NSApp.terminate(nil) } }
                }
            } catch { self.alert("Could not disable launch at login. Installation retained: \(error.localizedDescription)") }
        }
    }
    @objc func quit() { if !progress.busy { NSApp.terminate(nil) } }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { progress.busy ? .terminateCancel : .terminateNow }
    func alert(_ text: String) { let dialog = NSAlert(); dialog.messageText = "Order Manager"; dialog.informativeText = text; dialog.runModal() }
    func confirm(_ title: String, _ text: String, button: String) -> Bool {
        let dialog = NSAlert(); dialog.messageText = title; dialog.informativeText = text; dialog.addButton(withTitle: button); dialog.addButton(withTitle: "Cancel"); NSApp.activate(ignoringOtherApps: true); return dialog.runModal() == .alertFirstButtonReturn
    }
}
let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()

#endif
