import Foundation
import Testing
@testable import GhostlineKit

/// The stock page lagged under the finger (the owner, 2026-11-03: "the app is laggy and when I touch the graph ... it
/// sometimes misses"). Measured: reading a 6-month chart's dates took 39 ms (a 5-year chart 381 ms), the page read them
/// six times a draw, and a smooth frame has 8 to 16 ms. The dates are now read once, when the chart arrives, with
/// readers made once.
@Suite struct ChartCostTests {
    /// What the old `Stamp.date` did on every call: two new formatters.
    private func oldDate(_ iso: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: iso) ?? ISO8601DateFormatter().date(from: iso)
    }

    private let stamps = ["2026-10-30T13:30:00+00:00", "2026-10-30T13:30:00Z", "2026-10-30T09:30:00-04:00",
                          "2026-10-30T09:00:00.123456+00:00", "2026-10-30T09:00:00.5Z", "2026-10-30", "not a date", ""]

    @Test func theNewReaderReadsEveryStampAsTheOldOneDid() throws {
        for s in stamps {
            let new = Stamp.date(s), old = oldDate(s)
            #expect((new == nil) == (old == nil), "\(s)")
            if let new, let old { #expect(abs(new.timeIntervalSince(old)) < 0.001, "\(s): \(new) against \(old)") }
        }
        #expect(Stamp.tradingDay("2026-10-30") != nil && Stamp.tradingDay("not a date") == nil)
        let back = try #require(Stamp.date("2026-10-30T09:00:00.250Z"))
        #expect(back.timeIntervalSince1970.truncatingRemainder(dividingBy: 1) == 0.25)      // the fraction is kept
    }

    @Test func theReaderIsSafeFromManyThreads() async {
        let expected = stamps.map(oldDate)
        let results = await withTaskGroup(of: [Date?].self) { group in
            for _ in 0..<16 { group.addTask { (0..<50).flatMap { _ in stamps.map(Stamp.date) } } }
            var all: [[Date?]] = []
            for await r in group { all.append(r) }
            return all
        }
        let want = Array((0..<50).flatMap { _ in expected }).map { $0?.timeIntervalSince1970.rounded() }
        for r in results { #expect(r.map { $0?.timeIntervalSince1970.rounded() } == want) }
    }

    private func chartJSON(days: Int) -> String {
        let start = Date(timeIntervalSince1970: 1_600_000_000)
        let iso = ISO8601DateFormatter()
        let bars = (0..<days).map { i -> String in
            let ts = iso.string(from: start.addingTimeInterval(Double(i) * 86_400)).replacingOccurrences(of: "Z", with: "+00:00")
            return #"{"ts":"\#(ts)","open":"1","high":"2","low":"1","close":"1.5","volume":"1","source":"alpaca","as_of":"\#(ts)","timeframe":"1D"}"#
        }
        let none = "[" + Array(repeating: "null", count: days).joined(separator: ",") + "]"
        return #"{"ticker":"X","timeframe":"5Y","interval":"1Day","bars":[\#(bars.joined(separator: ","))],"source":"alpaca","as_of":null,"sma20":\#(none),"sma50":\#(none),"sma200":\#(none),"rsi14":\#(none),"macd":{"macd":\#(none),"signal":\#(none),"histogram":\#(none)},"bollinger":{"middle":\#(none),"upper":\#(none),"lower":\#(none)},"levels":[]}"#
    }

    @Test func aFiveYearChartReadsItsDatesOnceAndQuickly() throws {
        let c = try GhostlineJSON.decoder().decode(ChartData.self, from: Data(chartJSON(days: 1250).utf8))
        #expect(c.plotDates.count == 1250 && c.instants.count == 1250 && c.plotDates.allSatisfy { $0 != nil })
        #expect(c.plotDates[3] == c.bars[3].date && c.instants[3] == c.bars[3].instant)
        let clock = ContinuousClock()
        let prepare = clock.measure {
            _ = ChartData.plotDates(bars: c.bars, instants: c.bars.map(\.instant), interval: c.interval)
        }
        #expect(prepare < .milliseconds(40), "preparing 1,250 bars took \(prepare); it took 381 ms before")
        let reads = clock.measure { for _ in 0..<1000 { _ = c.plotDates.count } }
        #expect(reads < .milliseconds(5), "a draw reads the stored dates, it never works them out again")
    }

    @Test func encodingKeepsOnlyTheBackendsFields() throws {
        let c = try GhostlineJSON.decoder().decode(ChartData.self, from: Data(chartJSON(days: 3).utf8))
        let again = try GhostlineJSON.decoder().decode(ChartData.self, from: GhostlineJSON.encoder().encode(c))
        #expect(again == c)
        let keys = try #require(try JSONSerialization.jsonObject(with: GhostlineJSON.encoder().encode(c)) as? [String: Any]).keys
        #expect(!keys.contains("plot_dates") && !keys.contains("instants"))
    }
}
