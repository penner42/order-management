import XCTest
import CFileLock

final class FileLockTests: XCTestCase {
    func testExclusiveLockRejectsContentionAndReleasesOnClose() throws {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        XCTAssertTrue(FileManager.default.createFile(atPath: path.path, contents: Data()))
        defer { try? FileManager.default.removeItem(at: path) }
        let first = try FileHandle(forUpdating: path)
        defer { try? first.close() }
        let second = try FileHandle(forUpdating: path)
        defer { try? second.close() }

        XCTAssertEqual(order_manager_try_lock(first.fileDescriptor), 0)
        XCTAssertEqual(order_manager_try_lock(second.fileDescriptor), -1)
        try first.close()
        XCTAssertEqual(order_manager_try_lock(second.fileDescriptor), 0)
    }

    func testInvalidDescriptorFails() {
        XCTAssertEqual(order_manager_try_lock(-1), -1)
    }
}
