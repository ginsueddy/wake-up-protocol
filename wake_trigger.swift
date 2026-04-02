import Cocoa
import Foundation

class WakeTrigger: NSObject {
    private let projectDir: String
    private var lastSpawnTime: Date = .distantPast
    private let debounceInterval: TimeInterval = 10

    override init() {
        self.projectDir = ProcessInfo.processInfo.environment["WAKE_UP_PROJECT_DIR"]
            ?? NSString(string: "~/github/wake-up-protocol").expandingTildeInPath
        super.init()

        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(onWakeOrUnlock),
            name: NSWorkspace.didWakeNotification, object: nil)

        DistributedNotificationCenter.default().addObserver(
            self, selector: #selector(onWakeOrUnlock),
            name: NSNotification.Name("com.apple.screenIsUnlocked"), object: nil)

        log("wake_trigger started — watching for wake/unlock events")
        log("project dir: \(projectDir)")
    }

    @objc private func onWakeOrUnlock(_ notification: Notification) {
        log("received \(notification.name.rawValue)")

        let now = Date()
        if now.timeIntervalSince(lastSpawnTime) < debounceInterval {
            log("debounce — skipping (last spawn \(String(format: "%.1f", now.timeIntervalSince(lastSpawnTime)))s ago)")
            return
        }

        if isAlreadyRunning() {
            log("wake_up.py already running — skipping")
            return
        }

        lastSpawnTime = now
        spawnListener()
    }

    private func isAlreadyRunning() -> Bool {
        let lockPath = NSTemporaryDirectory() + "wake_up_protocol.lock"
        guard let contents = try? String(contentsOfFile: lockPath, encoding: .utf8),
              let pid = Int32(contents.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            return false
        }
        return kill(pid, 0) == 0
    }

    private func spawnListener() {
        let python = projectDir + "/.venv/bin/python3"
        let script = projectDir + "/wake_up.py"

        guard FileManager.default.fileExists(atPath: python) else {
            log("ERROR: python not found at \(python)")
            return
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [script, "--timeout", "120"]
        process.currentDirectoryURL = URL(fileURLWithPath: projectDir)

        do {
            try process.run()
            log("spawned wake_up.py (pid \(process.processIdentifier))")
        } catch {
            log("ERROR: failed to spawn wake_up.py: \(error)")
        }
    }

    private func log(_ message: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        let timestamp = formatter.string(from: Date())
        FileHandle.standardError.write(Data("\(timestamp)  \(message)\n".utf8))
    }
}

let trigger = WakeTrigger()
RunLoop.main.run()
