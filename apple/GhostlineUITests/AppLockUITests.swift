import XCTest
import notify

/// The app lock on the real app (sign-in check, lock, tabs) against fixed answers. The owner, 2026-11-03: "multiple
/// attempts still not able to login". The lock asked for Face ID while the app was leaving, that request failed, and
/// on return it never asked again.
///
/// The simulator takes its Face ID from two system notifications, which the test posts itself. No test here taps the
/// unlock button unless its name says so: the face alone unlocks, so a lock that does not ask by itself fails.
@MainActor
final class AppLockUITests: XCTestCase {
    private func launch(grace: Int? = nil) -> XCUIApplication {
        continueAfterFailure = false
        enrolFace()
        let app = XCUIApplication()
        app.launchArguments = ["-uiTestScreen", "app"] + (grace.map { ["-appLockGrace", String($0)] } ?? [])
        app.launch()
        XCTAssertTrue(unlockButton(app).waitForExistence(timeout: 20), "the lock screen at launch")
        return app
    }

    private func unlockButton(_ app: XCUIApplication) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Unlock with")).firstMatch
    }

    private func enrolFace() {
        var token: Int32 = 0
        let key = "com.apple.BiometricKit.enrollmentChanged"
        notify_register_check(key, &token)
        notify_set_state(token, 1)
        notify_post(key)
    }

    /// Holds a matching face in front of the phone until the lock screen goes. It answers only a request in progress.
    private func faceUnlocks(_ app: XCUIApplication, _ moment: String, line: UInt = #line) {
        for _ in 0..<20 {
            notify_post("com.apple.BiometricKit_Sim.pearl.match")
            sleep(1)
            if !unlockButton(app).exists { return }
        }
        let alert = XCUIApplication(bundleIdentifier: "com.apple.springboard").alerts.firstMatch
        let why = alert.exists ? "a system alert is in the way: \(alert.label)" : "nothing asked"
        XCTFail("still locked \(moment), with a matching face in view for 20 s: \(why)", line: line)
    }

    func testTheLockAsksByItselfAtLaunchAndAfterAnAbsence() {
        let app = launch(grace: 3)
        faceUnlocks(app, "at launch")
        XCUIDevice.shared.press(.home)
        sleep(5)
        app.activate()
        XCTAssertTrue(unlockButton(app).waitForExistence(timeout: 5), "locked again after more than the grace")
        faceUnlocks(app, "after coming back")
    }

    func testAShortAbsenceDoesNotLock() {
        let app = launch()                  // the real grace, one minute
        faceUnlocks(app, "at launch")
        XCUIDevice.shared.press(.home)
        app.activate()
        sleep(2)
        XCTAssertFalse(unlockButton(app).exists, "open again, with no question")
    }

    func testLeavingDuringThePromptAsksAgainOnReturn() {
        let app = launch()
        sleep(2)                            // the prompt is up, and no face answers it
        XCUIDevice.shared.press(.home)
        sleep(2)
        app.activate()
        faceUnlocks(app, "after leaving during the prompt")
    }

    func testACancelStaysCancelledUntilTheButtonIsTapped() {
        let app = launch()
        sleep(2)
        notify_post("com.apple.BiometricKit_Sim.pearl.nomatch")
        let system = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let cancel = system.buttons["com.apple.localauthentication.ax.authentication.button.cancel"].firstMatch
        XCTAssertTrue(cancel.waitForExistence(timeout: 10), "the system offers Cancel after a face it does not know")
        cancel.tap()
        XCTAssertTrue(app.staticTexts["Cancelled. Tap Unlock to try again."].waitForExistence(timeout: 5))
        notify_post("com.apple.BiometricKit_Sim.pearl.match")
        sleep(3)
        XCTAssertTrue(unlockButton(app).exists, "nothing asks after a cancel, so a face in view opens nothing")
        unlockButton(app).tap()
        faceUnlocks(app, "after the button")
    }
}
