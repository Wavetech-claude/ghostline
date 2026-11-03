import Foundation
import GhostlineKit
import Testing
@testable import Ghostline

/// The preview a search suggestion opens (the owner, 2026-11-02): its price and change, and the fixed answers the UI
/// tests run on, which must decode as the real backend's do.
@MainActor
@Suite struct TickerPreviewTests {
    private func chart(_ json: String) throws -> ChartData {
        try GhostlineJSON.decoder().decode(ChartData.self, from: Data(json.utf8))
    }

    @Test func thePriceIsTheLatestCloseAndTheChangeIsFromThePreviousClose() throws {
        let q = TickerQuote(chart: try chart(FixtureProtocol.chart("NFLX")))
        #expect(q.price == "110.50")
        #expect(q.changeMoney.flatMap { Decimal(string: $0) } == Decimal(string: "10.50"))
        #expect(q.changePercent.flatMap { Decimal(string: $0) } == Decimal(string: "10.5"))
        #expect(q.when == "At the close")
    }

    @Test func noPreviousCloseMeansNoChangeNotAGuess() throws {
        let json = FixtureProtocol.chart("NFLX").replacingOccurrences(of: #""previous_close": "100.00""#,
                                                                       with: #""previous_close": null"#)
        let q = TickerQuote(chart: try chart(json))
        #expect(q.price == "110.50" && q.changeMoney == nil && q.changePercent == nil)
    }

    @Test func noBarsMeansNoPrice() {
        #expect(TickerQuote(chart: nil).price == nil)
    }

    @Test func useNamesTheTicker() {
        let m = TickerMatch(ticker: "NFLX", name: "Netflix Inc.", watched: false)
        #expect(TickerAction.use().title(m) == "Use NFLX")
        #expect(TickerAction.use().done(m) == nil)
    }

    @Test func theFixedAnswersDecode() throws {
        let p = try GhostlineJSON.decoder().decode(Portfolio.self, from: Data(FixtureProtocol.portfolio.utf8))
        #expect(p.books.first?.positions.first?.quantity == "3")
        let url = URL(string: "https://fixture.invalid/market/search?q=net&limit=6")!
        let (code, body) = FixtureProtocol.answer(method: "GET", url: url, body: Data())
        let found = try GhostlineJSON.decoder().decode(TickerMatches.self, from: Data(body.utf8))
        #expect(code == 200 && found.results.map(\.ticker) == ["NFLX"])
    }
}
