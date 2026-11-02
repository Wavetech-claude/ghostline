import Foundation
import Testing
@testable import GhostlineKit

/// The 1D and 1W charts: intraday bars keep their moment, and the nights between sessions are taken out of the x axis.
@Suite struct IntradayTests {
    private func bar(_ ts: String, _ close: String, _ tf: String = "30Min") -> String {
        #"{"ticker":"AAPL","timeframe":"\#(tf)","ts":"\#(ts)","open":"1","high":"2","low":"0.5","close":"\#(close)","volume":"10","source":"alpaca","as_of":"2026-10-30T20:00:00+00:00"}"#
    }

    private func chart(_ bars: [String], interval: String = "30Min", timeframe: String = "1W") throws -> ChartData {
        let json = #"{"ticker":"AAPL","timeframe":"\#(timeframe)","interval":"\#(interval)","feed":"iex","delayed":false,"previous_close":"99.5000","market_open":true,"bars":[\#(bars.joined(separator: ","))],"source":"alpaca","as_of":"2026-10-30T19:58:00+00:00","sma20":[],"sma50":[],"sma200":[],"rsi14":[],"macd":{"macd":[],"signal":[],"histogram":[]},"bollinger":{"middle":[],"upper":[],"lower":[]},"levels":[]}"#
        return try GhostlineJSON.decoder().decode(ChartData.self, from: Data(json.utf8))
    }

    @Test func anIntradayBarKeepsItsMoment() throws {
        let b = try GhostlineJSON.decoder().decode(BarRow.self, from: Data(bar("2026-10-30T19:30:00+00:00", "1").utf8))
        #expect(b.isIntraday && b.date == Stamp.date("2026-10-30T19:30:00+00:00"))
        let daily = try GhostlineJSON.decoder().decode(BarRow.self, from: Data(bar("2026-10-01T04:00:00+00:00", "1", "1D").utf8))
        let day = try #require(daily.date)
        #expect(!daily.isIntraday && Calendar.current.component(.hour, from: day) == 0)
    }

    @Test func theNightBetweenTwoSessionsIsOneStepOnTheAxis() throws {
        // Thursday's last two bars, then Friday's first: 17.5 hours apart in time, one 30-minute step on the axis
        let c = try chart([bar("2026-10-29T19:00:00+00:00", "1"), bar("2026-10-29T19:30:00+00:00", "2"),
                           bar("2026-10-30T13:30:00+00:00", "3")])
        let x = c.plotDates.compactMap { $0 }
        #expect(x.count == 3)
        #expect(x[1].timeIntervalSince(x[0]) == 1800 && x[2].timeIntervalSince(x[1]) == 1800)
        #expect(c.bar(nearest: x[2])?.close == "3" && c.plotDate(of: c.bars[2]) == x[2])
        #expect(c.isIntraday && c.previousClose == "99.5000" && c.marketOpen == true)
    }

    @Test func theSourceLineSaysWhereAndHowFresh() throws {
        let c = try chart([bar("2026-10-30T19:30:00+00:00", "1", "5Min")], interval: "5Min", timeframe: "1D")
        #expect(c.sourceLine.hasPrefix("Alpaca IEX · 5-min bars · as of ") && c.sourceLine.hasSuffix(" · market open"))
    }

    @Test func aDailyChartFromBeforeIntradayStillDecodes() throws {
        let json = #"{"ticker":"AAPL","timeframe":"6M","bars":[],"source":null,"as_of":null,"sma20":[],"sma50":[],"sma200":[],"rsi14":[],"macd":{"macd":[],"signal":[],"histogram":[]},"bollinger":{"middle":[],"upper":[],"lower":[]},"levels":[]}"#
        let c = try GhostlineJSON.decoder().decode(ChartData.self, from: Data(json.utf8))
        #expect(!c.isIntraday && c.plotDates.isEmpty && c.sourceLine.hasPrefix("? · daily bars"))
    }
}
