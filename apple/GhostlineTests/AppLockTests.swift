import Foundation
import LocalAuthentication
import Testing
@testable import Ghostline

/// A device prompt under the test's control: each ask waits until the test answers it.
@MainActor
private final class FakePrompt: LockPrompt {
    var cannotAsk: Error?
    private(set) var asks = 0
    private var waiting: [CheckedContinuation<Bool, Error>] = []
    var stillWaiting: Int { waiting.count }

    func unavailable() -> Error? { cannotAsk }

    func ask() async throws -> Bool {
        asks += 1
        return try await withCheckedThrowingContinuation { waiting.append($0) }
    }

    /// The system ends the request the app replaced, as LAContext.invalidate() does.
    func cancel() { answer(.failure(LAError(.appCancel))) }

    func answer(_ result: Result<Bool, Error>) {
        guard !waiting.isEmpty else { return }
        waiting.removeFirst().resume(with: result)
    }
}

/// The app lock (the owner, 2026-11-01): locked at launch when on, again after more than a minute away. It asks by
/// itself once each time it locks, and only one request is alive at a time (the owner, 2026-11-03: "multiple attempts
/// still not able to login").
@MainActor
@Suite struct AppLockTests {
    private func lock(stored: Bool? = nil, prompt: LockPrompt = FakePrompt()) -> AppLock {
        let defaults = UserDefaults(suiteName: "AppLockTests-\(UUID().uuidString)")!
        if let stored { defaults.set(stored, forKey: AppLock.enabledKey) }
        return AppLock(defaults: defaults, prompt: prompt)
    }

    /// Lets a started request reach the prompt, and an answered one reach the lock.
    private func settle() async {
        for _ in 0..<20 { await Task.yield() }
    }

    /// Unlocked, as after a successful Face ID, and ready to lock again.
    private func opened(_ l: AppLock, _ p: FakePrompt) async {
        let done = Task { await l.askByItself() }
        await settle()
        p.answer(.success(true))
        await done.value
        #expect(!l.locked)
    }

    // MARK: when it locks

    @Test func isOnAndLockedAtLaunchByDefault() {
        let l = lock()
        #expect(l.enabled && l.locked)
    }

    @Test func turnedOffItNeitherLocksNorLocksAgain() {
        let l = lock(stored: false)
        #expect(!l.enabled && !l.locked)
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(3600))
        #expect(!l.locked)
    }

    @Test func aShortAbsenceStaysOpenALongOneLocks() async {
        let l = lock()
        l.enabled = false          // unlocked, as after a successful Face ID
        l.enabled = true
        #expect(!l.locked)
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(AppLock.grace - 1))
        #expect(!l.locked)
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(AppLock.grace + 1))
        #expect(l.locked)
    }

    @Test func turningItOffUnlocks() {
        let l = lock()
        #expect(l.locked)
        l.enabled = false
        #expect(!l.locked)
    }

    @Test func theSwitchIsRemembered() {
        let defaults = UserDefaults(suiteName: "AppLockTests-\(UUID().uuidString)")!
        let l = AppLock(defaults: defaults, prompt: FakePrompt())
        l.enabled = false
        #expect(AppLock(defaults: defaults, prompt: FakePrompt()).enabled == false)
    }

    // MARK: asking by itself

    @Test func itAsksByItselfOnceAtLaunchAndUnlocks() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        #expect(l.asksByItself)
        let done = Task { await l.askByItself() }
        await settle()
        #expect(p.asks == 1)
        #expect(!l.asksByItself, "only once")
        p.answer(.success(true))
        await done.value
        #expect(!l.locked && l.message == nil)
    }

    @Test func itAsksByItselfAgainEachTimeItLocks() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        await opened(l, p)
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(AppLock.grace + 1))
        #expect(l.locked && l.asksByItself)
        let done = Task { await l.askByItself() }
        await settle()
        #expect(p.asks == 2)
        p.answer(.success(true))
        await done.value
        #expect(!l.locked)
    }

    @Test func aStaleMessageIsGoneWhenItLocksAgain() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let done = Task { await l.askByItself() }
        await settle()
        p.answer(.failure(LAError(.userCancel)))
        await done.value
        #expect(l.message != nil)
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(AppLock.grace + 1))
        #expect(l.message == nil)
    }

    @Test func aShortAbsenceDoesNotAsk() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        await opened(l, p)
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(1))
        #expect(!l.asksByItself)
    }

    @Test func aLockThatIsOffNeverAsksByItself() {
        #expect(!lock(stored: false).asksByItself)
    }

    @Test func askingByItselfDoesNothingWhenItShouldNotAsk() async {
        let p = FakePrompt()
        let l = lock(stored: false, prompt: p)
        await l.askByItself()
        #expect(p.asks == 0)
    }

    // MARK: after a failure

    @Test func aCancelStaysCancelledUntilTheButton() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let first = Task { await l.askByItself() }
        await settle()
        p.answer(.failure(LAError(.userCancel)))
        await first.value
        #expect(l.locked && !l.asksByItself, "a cancel never brings the prompt back by itself")
        #expect(l.message == "Cancelled. Tap Unlock to try again.")
        // coming back from a short trip does not bring it back either
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(1))
        #expect(!l.asksByItself)
        // the button asks
        let second = Task { await l.unlock() }
        await settle()
        #expect(p.asks == 2)
        #expect(l.message == nil, "no old message under the prompt")
        p.answer(.success(true))
        await second.value
        #expect(!l.locked)
    }

    @Test(arguments: [LAError.Code.systemCancel, .appCancel, .notInteractive])
    func theSystemTakingThePromptAwayAsksAgainWithNoMessage(code: LAError.Code) async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let done = Task { await l.askByItself() }
        await settle()
        p.answer(.failure(LAError(code)))
        await done.value
        #expect(l.locked && l.message == nil)
        #expect(l.asksByItself, "the next time the app is on screen")
    }

    @Test func aSystemThatKeepsCancellingNeverMakesALoop() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        for _ in 0..<2 {            // the first ask, and the one ask again
            let done = Task { await l.askByItself() }
            await settle()
            p.answer(.failure(LAError(.systemCancel)))
            await done.value
        }
        #expect(p.asks == 2)
        #expect(!l.asksByItself, "no third ask with no return in between")
        #expect(l.message?.hasSuffix("was interrupted. Tap Unlock to try again.") == true)
        l.didReturn()               // the prompt closing makes the app active again: that is not a return
        #expect(!l.asksByItself)
    }

    @Test func eachRealAbsenceDuringThePromptAsksAgain() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        for _ in 0..<3 {
            let done = Task { await l.askByItself() }
            await settle()
            let t = Date()
            l.didLeave(at: t)
            p.answer(.failure(LAError(.systemCancel)))
            await done.value
            l.didReturn(at: t.addingTimeInterval(1))
            #expect(l.asksByItself && l.message == nil)
        }
        #expect(p.asks == 3)
    }

    @Test func onlyAYesOpensIt() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let done = Task { await l.unlock() }
        await settle()
        p.answer(.success(false))
        await done.value
        #expect(l.locked)
    }

    @Test func tooManyTriesSaysToUseThePasscode() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let done = Task { await l.unlock() }
        await settle()
        p.answer(.failure(LAError(.biometryLockout)))
        await done.value
        #expect(l.message == "Too many tries. Unlock the device with its passcode, then try again.")
    }

    @Test func aFaceThatDoesNotMatchSaysSo() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let done = Task { await l.unlock() }
        await settle()
        p.answer(.failure(LAError(.authenticationFailed)))
        await done.value
        #expect(l.message?.hasSuffix("did not recognize you. Tap Unlock to try again.") == true)
    }

    @Test func anyOtherFailureShowsTheSystemsOwnReason() async {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let reason = NSError(domain: "test", code: 1, userInfo: [NSLocalizedDescriptionKey: "The sensor is covered."])
        let done = Task { await l.unlock() }
        await settle()
        p.answer(.failure(reason))
        await done.value
        #expect(l.message == "The sensor is covered.")
    }

    @Test func noPasscodeOffersToContinueWithoutTheLock() async {
        let p = FakePrompt()
        p.cannotAsk = LAError(.passcodeNotSet)
        let l = lock(prompt: p)
        await l.askByItself()
        #expect(l.needsPasscode && p.asks == 0)
        #expect(l.message?.contains("no passcode") == true)
    }

    @Test func aPasscodeSetLaterEndsThatOffer() async {
        let p = FakePrompt()
        p.cannotAsk = LAError(.passcodeNotSet)
        let l = lock(prompt: p)
        await l.unlock()
        #expect(l.needsPasscode)
        p.cannotAsk = nil
        let done = Task { await l.unlock() }
        await settle()
        #expect(!l.needsPasscode && l.message == nil)
        p.answer(.success(true))
        await done.value
        #expect(!l.locked)
    }

    // MARK: one request at a time

    @Test func aTapReplacesARequestStillWaiting() async throws {
        let p = FakePrompt()
        let l = lock(prompt: p)
        let first = Task { await l.askByItself() }
        await settle()
        #expect(p.asks == 1)
        let second = Task { await l.unlock() }     // the button: the first must not keep it dead
        await settle()
        #expect(p.asks == 2)
        // Stops here if the old request did not end: waiting for it below would hang the whole test run.
        try #require(p.stillWaiting == 1, "the old request ended, so only the new one waits")
        await first.value                          // it writes nothing
        #expect(l.locked && l.message == nil && !l.asksByItself)
        p.answer(.success(true))
        await second.value
        #expect(!l.locked)
    }
}
