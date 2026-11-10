import Foundation

struct HelperEvent {
    let type: String
    let data: Any
    static func decode(_ line: Data) -> HelperEvent? {
        guard let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
              let type = object["type"], let data = object["data"], let name = type as? String else { return nil }
        return HelperEvent(type: name, data: data)
    }
}

// Keeps progress/error text independent of window visibility and requires both a
// successful exit and a completion event before presenting success.
struct ProgressState {
    var step = "Preparing Order Manager"
    var activity = ""
    var busy = false
    var completed = false
    var error: String?
    mutating func begin() { step = "Preparing Order Manager"; activity = ""; busy = true; completed = false; error = nil }
    mutating func receive(_ event: HelperEvent) {
        switch event.type {
        case "progress": step = event.data as? String ?? step
        case "activity": activity += (event.data as? String ?? "") + "\n"; activity = String(activity.suffix(60000))
        case "error": error = event.data as? String ?? "Operation failed"
        case "complete": completed = (event.data as? Bool) == true
        default: break
        }
    }
    mutating func finish(_ code: Int32) {
        busy = false
        if code != 0 || !completed || error != nil {
            step = "Setup could not finish"
            error = error ?? "The helper stopped before completing. View Logs and retry."
        } else { step = "Complete" }
    }
}
protocol HelperRunning {
    func run(_ arguments: [String], event: @escaping (HelperEvent) -> Void, completion: @escaping (Int32) -> Void)
}
final class HelperClient: HelperRunning {
    let executable: URL
    init(bundle: Bundle = .main) { executable = bundle.bundleURL.appendingPathComponent("Contents/Resources/helper/OrderManager.Mac") }
    func run(_ arguments: [String], event: @escaping (HelperEvent) -> Void, completion: @escaping (Int32) -> Void) {
        let process = Process(); process.executableURL = executable; process.arguments = arguments
        let output = Pipe(); process.standardOutput = output; process.standardError = FileHandle.nullDevice
        do { try process.run() }
        catch { event(HelperEvent(type: "error", data: error.localizedDescription)); completion(1); return }
        DispatchQueue.global(qos: .utility).async {
            var pending = Data()
            // Drain before wait/exit callback, preserving event order even for short operations.
            while true {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                pending.append(chunk)
                while let newline = pending.firstIndex(of: 10) {
                    let line = pending.subdata(in: 0..<newline); pending.removeSubrange(0...newline)
                    if let decoded = HelperEvent.decode(line) { DispatchQueue.main.async { event(decoded) } }
                }
            }
            process.waitUntilExit()
            let result = process.terminationStatus
            DispatchQueue.main.async { completion(result) }
        }
    }
}
