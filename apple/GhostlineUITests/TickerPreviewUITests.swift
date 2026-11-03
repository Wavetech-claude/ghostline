import XCTest

/// A tap on a search suggestion opens the stock's preview, with the action of the screen it was opened from (the
/// owner, 2026-11-02: "when you click/tap it, it shows a preview with action button based on where you are on the
/// app"). Before this, a tap only filled the box, and the owner read that as "nothing happens".
///
/// Each test opens one real screen, presented as the app presents it, against fixed answers (UITestFixture, Debug
/// builds only): NFLX is held (3 shares in Real) and not watched, NVDA is watched, and the 1D chart closes at $110.50.
@MainActor
final class TickerPreviewUITests: XCTestCase {
    private func launch(_ screen: String) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-uiTestScreen", screen]
        app.launch()
        return app
    }

    private func element(_ app: XCUIApplication, _ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id].firstMatch
    }

    private func button(_ scope: XCUIElement, beginningWith text: String) -> XCUIElement {
        scope.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", text)).firstMatch
    }

    /// Types `query` into the field, taps the suggestion for `ticker`, and checks that the preview opened. A ticker
    /// field lists its suggestions in one container; the Search tab lists them as its rows.
    private func openPreview(_ app: XCUIApplication, field: XCUIElement, query: String = "net", ticker: String = "NFLX",
                             inField: Bool = true) {
        XCTAssertTrue(field.waitForExistence(timeout: 15), "the field is on screen")
        field.tap()
        field.typeText(query)
        let suggestion = button(inField ? element(app, "suggestions") : app, beginningWith: ticker)
        XCTAssertTrue(suggestion.waitForExistence(timeout: 5), "the suggestion for \(ticker) is listed")
        suggestion.tap()
        let price = element(app, "previewPrice")
        XCTAssertTrue(price.waitForExistence(timeout: 5), "the tap opened the preview")
        XCTAssertEqual(price.label, "$110.50")
    }

    private func expectValue(_ field: XCUIElement, _ value: String) {
        let filled = expectation(for: NSPredicate(format: "value == %@", value), evaluatedWith: field)
        wait(for: [filled], timeout: 5)
    }

    // MARK: the screens

    func testWatchlistPreviewAddsTheStockAndClosesTheSheet() {
        let app = launch("watchlist")
        app.buttons["Add Ticker"].firstMatch.tap()
        let field = app.textFields["Ticker or company"]
        openPreview(app, field: field)
        XCTAssertTrue(element(app, "previewChange").label.contains("+$10.50"), "today's change against the previous close")
        XCTAssertTrue(element(app, "previewMarks").label.contains("You hold 3 in Real"))
        app.buttons["Add to Watchlist"].tap()
        let gone = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: field)
        wait(for: [gone], timeout: 5)
        XCTAssertTrue(button(app, beginningWith: "NFLX").waitForExistence(timeout: 5), "NFLX is on the watchlist")
    }

    func testWatchlistPreviewOfAWatchedStockSaysWatching() {
        let app = launch("watchlist")
        app.buttons["Add Ticker"].firstMatch.tap()
        openPreview(app, field: app.textFields["Ticker or company"], query: "nv", ticker: "NVDA")
        let watching = app.buttons["Watching"]
        XCTAssertTrue(watching.waitForExistence(timeout: 5))
        XCTAssertFalse(watching.isEnabled)
    }

    func testAlertPreviewSetsTheTickerAndShowsThePrice() {
        let app = launch("alert")
        let field = app.textFields["Ticker"]
        openPreview(app, field: field)
        app.buttons["Set Alert for NFLX"].tap()
        expectValue(field, "NFLX")
        XCTAssertTrue(element(app, "chosenPrice").waitForExistence(timeout: 5), "the form shows the price")
    }

    func testPracticeOrderPreviewUsesTheStockAndShowsThePrice() {
        let app = launch("order")
        let field = app.textFields["Ticker"]
        openPreview(app, field: field)
        app.buttons["Use NFLX"].tap()
        expectValue(field, "NFLX")
        XCTAssertTrue(element(app, "chosenPrice").waitForExistence(timeout: 5), "the ticket shows the price")
    }

    func testTradeFormPreviewUsesTheStock() {
        let app = launch("trade")
        let field = app.textFields["Ticker"]
        openPreview(app, field: field)
        app.buttons["Use NFLX"].tap()
        expectValue(field, "NFLX")
    }

    func testBrokerStatementPreviewUsesTheStock() {
        let app = launch("statement")
        let field = app.textFields["Ticker"]
        openPreview(app, field: field)
        app.buttons["Use NFLX"].tap()
        expectValue(field, "NFLX")
    }

    func testClosingThePreviewLeavesTheFieldAsTyped() {
        let app = launch("trade")
        let field = app.textFields["Ticker"]
        openPreview(app, field: field)
        app.buttons["Close"].tap()
        expectValue(field, "net")
    }

    func testSearchPreviewWatchesThenOpensTheFullPage() {
        let app = launch("search")
        openPreview(app, field: app.searchFields.firstMatch, inField: false)
        app.buttons["Watch"].tap()
        XCTAssertTrue(app.buttons["Watching"].waitForExistence(timeout: 5), "Watch reached the server and changed")
        app.buttons["Open Full Page"].tap()
        XCTAssertTrue(app.navigationBars["NFLX"].waitForExistence(timeout: 5), "the stock page opened")
    }

    func testSearchPreviewPracticeBuyOpensTheTicketForTheStock() {
        let app = launch("search")
        openPreview(app, field: app.searchFields.firstMatch, inField: false)
        app.buttons["Practice Buy"].tap()
        let field = app.textFields["Ticker"]
        XCTAssertTrue(field.waitForExistence(timeout: 5), "the practice ticket opened")
        expectValue(field, "NFLX")
    }
}
