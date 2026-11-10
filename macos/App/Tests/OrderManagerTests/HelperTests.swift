import XCTest
@testable import OrderManager

final class ScriptedHelper: HelperRunning {
    var events: [HelperEvent] = []
    var exit: Int32 = 0
    func run(_ arguments: [String], event: @escaping (HelperEvent) -> Void, completion: @escaping (Int32) -> Void) {
        events.forEach(event); completion(exit)
    }
}
final class HelperTests: XCTestCase {
    func testProgressSurvivesWindowClosureAndCompletesOnlyAfterExit() {
        let helper = ScriptedHelper()
        helper.events = [HelperEvent(type: "progress", data: "Waiting for Docker Desktop"), HelperEvent(type: "activity", data: "Complete first-run prompts"), HelperEvent(type: "complete", data: true)]
        var state = ProgressState(); state.begin()
        helper.run(["start"], event: { state.receive($0) }, completion: { state.finish($0) })
        XCTAssertEqual(state.step, "Complete"); XCTAssertFalse(state.busy)
        XCTAssertTrue(state.activity.contains("first-run prompts"))
    }
    func testFailureAndRetry() {
        var state = ProgressState(); state.begin()
        state.receive(HelperEvent(type: "error", data: "Recovery required")); state.finish(1)
        XCTAssertEqual(state.error, "Recovery required"); XCTAssertNotEqual(state.step, "Complete")
        state.begin(); XCTAssertNil(state.error); XCTAssertTrue(state.busy)
        state.receive(HelperEvent(type: "complete", data: true)); state.finish(0); XCTAssertEqual(state.step, "Complete")
    }
    func testUnexpectedExitNeverLooksSuccessful() {
        var state = ProgressState(); state.begin(); state.finish(0)
        XCTAssertNotNil(state.error)
        state.begin(); state.receive(HelperEvent(type: "complete", data: true)); state.finish(1)
        XCTAssertNotNil(state.error)
    }
    func testStructuredErrorsAndStartupStatus() {
        let event = HelperEvent.decode(Data(#"{"type":"state","data":{"autoStart":true,"recoveryRequired":true}}"#.utf8))
        XCTAssertEqual(event?.type, "state")
        XCTAssertEqual((event?.data as? [String: Any])?["recoveryRequired"] as? Bool, true)
        XCTAssertNil(HelperEvent.decode(Data("Docker log text".utf8)))
        var state = ProgressState(); state.begin(); state.receive(HelperEvent(type: "error", data: "Login startup approval required")); state.finish(1)
        XCTAssertEqual(state.error, "Login startup approval required")
    }
}
