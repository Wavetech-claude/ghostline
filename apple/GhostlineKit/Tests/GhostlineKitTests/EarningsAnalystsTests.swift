import Foundation
import Testing
@testable import GhostlineKit

@Suite struct EarningsAnalystsTests {
    @Test func theCalendarDecodesAndGroupsByDay() throws {
        let json = #"""
        {"from":"2026-10-25","until":"2026-12-01","earnings":[
         {"ticker":"AAPL","name":"Apple Inc.","at":"2026-10-29","hour":"amc","estimate":"1.0000","actual":"1.1000","revenue_estimate":"115660327009.00","revenue_actual":null,"surprise_pct":"10.00","why":["watched"],"move_pct":"4.00","typical_move_pct":null},
         {"ticker":"MSFT","name":null,"at":"2026-11-04","hour":null,"estimate":"3.1000","actual":null,"revenue_estimate":null,"revenue_actual":null,"surprise_pct":null,"why":["held","idea"],"move_pct":null,"typical_move_pct":"6.33"},
         {"ticker":"NVDA","name":"NVIDIA CORP","at":"2026-11-04","hour":"bmo","estimate":null,"actual":null,"revenue_estimate":null,"revenue_actual":null,"surprise_pct":null,"why":["idea"],"move_pct":null,"typical_move_pct":null}]}
        """#
        let c = try GhostlineJSON.decoder().decode(EarningsCalendar.self, from: Data(json.utf8))
        #expect(c.days.map(\.day) == ["2026-10-29", "2026-11-04"] && c.days[1].reports.count == 2)
        #expect(c.earnings[0].surprisePct == "10.00" && c.earnings[1].typicalMovePct == "6.33")
        #expect(ReportHour.words(c.earnings[0].hour) == "After the close" && ReportHour.words(nil) == nil)
    }

    @Test func analystViewsDecode() throws {
        let json = #"""
        {"ticker":"AAPL","trends":[{"period":"2026-11-01","strong_buy":11,"buy":19,"hold":15,"sell":3,"strong_sell":1,"source":"finnhub","as_of":"2026-11-01T14:00:00+00:00"}],
         "consensus":{"strong_buy":1,"buy":69,"hold":33,"sell":9,"strong_sell":0,"consensus":"Buy","target_high":"400.0000","target_low":"245.0000","target_consensus":"340.8800","target_median":"355.0000","source":"fmp","as_of":"2026-11-01T14:00:00+00:00"}}
        """#
        let a = try GhostlineJSON.decoder().decode(AnalystViews.self, from: Data(json.utf8))
        #expect(a.trends.first?.total == 49 && a.consensus?.targetConsensus == "340.8800" && !a.isEmpty)
        let none = try GhostlineJSON.decoder().decode(AnalystViews.self,
                                                       from: Data(#"{"ticker":"SPY","trends":[],"consensus":null}"#.utf8))
        #expect(none.isEmpty)
    }

    @Test func searchMatchesDecodeWithAndWithoutTheirKind() throws {
        let json = #"{"results":[{"ticker":"NVDA","name":"NVIDIA Corp","watched":false,"held":true,"kind":"CS"},{"ticker":"QQQ","name":"Invesco QQQ Trust","watched":true,"held":false,"kind":"ETF"},{"ticker":"OLD","name":"An older answer","watched":false}]}"#
        let m = try GhostlineJSON.decoder().decode(TickerMatches.self, from: Data(json.utf8)).results
        #expect(m[0].held == true && m[0].badge == nil && m[1].badge == "ETF" && m[2].kind == nil && m[2].held == nil)
    }

    @Test func anOldEarningsRowStillDecodes() throws {
        let json = #"{"ticker":"AAPL","at":"2026-10-29","estimate":"1.0","actual":null,"source":"finnhub","as_of":"2026-10-30T00:00:00+00:00","move_pct":null}"#
        let e = try GhostlineJSON.decoder().decode(EarningsRow.self, from: Data(json.utf8))
        #expect(e.hour == nil && e.revenueEstimate == nil)
    }
}
