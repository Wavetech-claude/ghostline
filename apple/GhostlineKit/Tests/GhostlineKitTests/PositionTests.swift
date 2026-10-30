import Foundation
import Testing
@testable import GhostlineKit

/// The answers below are what the backend returned in its own tests (2026-10-30), trimmed.
@Suite struct PositionTests {
    @Test func decodesAPositionWithThesisMemoNewsAndNextReport() throws {
        let json = #"""
        {"book":"practice","ticker":"AAPL","quantity":"15","average_cost":"87.40","cost_basis":"1311.00","price":"103.0000","price_source":"alpaca","price_as_of":"2026-10-30T16:56:59.326837+00:00","value":"1545.00","day_pnl":"34.00","unrealized_pnl":"234.00","realized_pnl":"0.00","total_pnl":"234.00",
         "thesis":{"idea_id":1,"lane":"swing","module":"vcp","entry":"100.0000","stop":"95.0000","target":"120.0000","status":"open","reason":"AAPL: a test idea"},
         "memo":{"id":1,"idea_id":1,"ticker":"AAPL","call":"wait","size_money":null,"bull":"up","base":null,"bear":null,"catalysts":null,"invalidation":null,"what_would_change":null,"confidence_low":null,"confidence_high":null,"grounded":true,"struck":[],"trace_id":null,"job_id":null,"created_at":"2026-10-30T16:56:59.411645+00:00"},
         "news":[{"id":1,"ticker":"AAPL","headline":"Apple does a thing","url":"https://n/1","published_at":"2026-10-30T16:56:59.443556+00:00","source":"finnhub","as_of":"2026-10-30T16:56:59.443556+00:00","tone":null}],
         "next_earnings":{"ticker":"AAPL","at":"2026-11-29","estimate":"2.1000","actual":null,"source":"finnhub","as_of":"2026-10-30T16:56:59.459747+00:00"}}
        """#
        let p = try GhostlineJSON.decoder().decode(PositionDetail.self, from: Data(json.utf8))
        #expect(p.dayPnl == "34.00" && p.totalPnl == "234.00" && p.realizedPnl == "0.00")
        #expect(p.thesis?.ideaId == 1 && p.thesis?.stop == "95.0000" && p.thesis?.reason == "AAPL: a test idea")
        #expect(p.memo?.call == "wait" && p.news.first?.headline == "Apple does a thing")
        #expect(p.nextEarnings?.at == "2026-11-29" && p.nextEarnings?.movePct == nil)
    }

    @Test func decodesAClosedPositionWithoutPriceOrThesis() throws {
        let json = #"{"book":"real","ticker":"MSFT","quantity":"0","average_cost":null,"cost_basis":null,"price":null,"price_source":null,"price_as_of":null,"value":"0.00","day_pnl":null,"unrealized_pnl":"0.00","realized_pnl":"-12.50","total_pnl":"-12.50","thesis":null,"memo":null,"news":[],"next_earnings":null}"#
        let p = try GhostlineJSON.decoder().decode(PositionDetail.self, from: Data(json.utf8))
        #expect(p.averageCost == nil && p.thesis == nil && p.memo == nil && p.totalPnl == "-12.50")
    }

    @Test func decodesTheBookDayPnlAndSpysLine() throws {
        let book = #"{"book":"practice","name":"Practice","cash":"3689.00","invested":"1545.00","total":"5234.00","priced":true,"net_deposits":"5000.00","total_pnl":"234.00","day_pnl":"34.00","day_pnl_partial":false,"positions":[{"ticker":"AAPL","quantity":"15","average_cost":"87.40","cost_basis":"1311.00","price":"103.0000","price_source":"alpaca","price_as_of":"2026-10-30T16:56:59.326837+00:00","value":"1545.00","unrealized_pnl":"234.00","day_pnl":"34.00","lane":null}],"allocation":[{"name":"none","share":"0.2952"}]}"#
        let b = try GhostlineJSON.decoder().decode(PortfolioBook.self, from: Data(book.utf8))
        #expect(b.dayPnl == "34.00" && b.dayPnlPartial == false && b.positions[0].dayPnl == "34.00")
        let series = #"{"book":"practice","points":[{"date":"2026-09-01","value":"1000.00","net_flow":"1000.00","twr_pct":"0.00","spy_pct":"0.00"},{"date":"2026-09-02","value":"1000.00","net_flow":"0.00","twr_pct":"0.00","spy_pct":"2.50"}],"twr_pct":"0.00"}"#
        let s = try GhostlineJSON.decoder().decode(BookSeries.self, from: Data(series.utf8))
        #expect(s.points.map(\.spyPct) == ["0.00", "2.50"] && s.twrPct == "0.00")
    }

    @Test func aBookFromAnOlderServerStillDecodes() throws {
        let book = #"{"book":"real","name":"Real","cash":"0.00","invested":"0.00","total":"0.00","priced":true,"net_deposits":"0.00","total_pnl":null,"positions":[],"allocation":[]}"#
        let b = try GhostlineJSON.decoder().decode(PortfolioBook.self, from: Data(book.utf8))
        #expect(b.dayPnl == nil && b.dayPnlPartial == nil)
    }

    @Test(arguments: ["America/Los_Angeles", "Asia/Kolkata", "UTC", "Pacific/Honolulu"])
    func aCalendarDayNeverMovesADay(zone: String) throws {
        let tz = try #require(TimeZone(identifier: zone))
        #expect(Stamp.day("2026-10-28", in: tz).contains("28"))
    }

    @Test func aMomentShowsTheDayWhereItIsRead() throws {
        let la = try #require(TimeZone(identifier: "America/Los_Angeles"))
        let utc = try #require(TimeZone(identifier: "UTC"))
        #expect(Stamp.day("2026-10-29T03:30:00Z", in: la).contains("28"))   // 20:30 on the 28th in Los Angeles
        #expect(Stamp.day("2026-10-29T03:30:00Z", in: utc).contains("29"))
    }
}
