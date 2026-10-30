import Foundation
import Testing
@testable import GhostlineKit

@Suite struct InvestModelTests {
    @Test func decodesAPlanWithARefusedSplit() throws {
        let json = #"""
        {"id":3,"amount":"50.00","for_date":"2026-11-02","book":"practice","unallocated":"0.00","status":"proposed","approved_to":null,
         "result":null,"created_at":"x","decided_at":null,"placed_at":null,"note":null,
         "splits":[{"idea_id":1,"ticker":"AAPL","lane":"swing","module":"vcp","score":"80.00","reason":"r","dollars":"50.00","price":"100.05",
                    "price_source":"tradier ask","shares":"0.49975","stop":"99","max_loss":"0.52","decision":"pass","why":"every rule passed"},
                   {"idea_id":2,"ticker":"MSFT","lane":"swing","module":"vcp","score":"60.00","reason":"r","dollars":"0.00","decision":"refuse",
                    "why":"earnings_blackout_days: the earnings date is unknown (its money moved to the ideas that pass)"}]}
        """#
        let p = try GhostlineJSON.decoder().decode(InvestPlan.self, from: Data(json.utf8))
        #expect(p.splits.count == 2 && p.splits[0].passed && !p.splits[1].passed && p.splits[1].price == nil)
        #expect(p.forDate == "2026-11-02" && p.approvedTo == nil)
    }

    @Test func decodesTheAITraderAgainstTheGhost() throws {
        let json = #"""
        {"state":{"book":"ai","cash":"7999.00","available":"7999.00","unsettled":"0.00","locked_collateral":"0.00","equity":"9999.00",
                  "open_positions":1,"short_puts":0,"last_runs":[{"kind":"entries","at":"x","result":{"placed":[]}}]},
         "trades":[{"id":5,"ticker":"AAPL","asset":"stock","side":"buy","quantity":"20","fill":"100.0500","fees":"0.00","at":"x","note":"AI trader, idea #1",
                    "option_symbol":null,"lane":"swing","idea_id":1,"module":"vcp","copy_real_allowed":false,"copy_real_reason":"the swing lane has not graduated in practice"}],
         "series":{"ai":[{"date":"2026-09-01","value":"1000.00","net_flow":"1000.00","twr_pct":"0.00"}],"ghost":[]},
         "twr_pct":{"ai":"0.00","ghost":null},"ghost_trades":[],
         "lanes":[{"lane":"swing","settled_n":0,"expectancy":null,"positive_expectancy":false,"graduated":false,"updated_at":"x"}]}
        """#
        let a = try GhostlineJSON.decoder().decode(AITraderAnswer.self, from: Data(json.utf8))
        #expect(a.state.openPositions == 1 && !a.trades[0].copyRealAllowed && a.twrPct.ghost == nil)
        #expect(a.series.ai[0].day != nil && a.lanes[0].lane == "swing")
    }

    @Test func alertRulesReadAsWords() throws {
        let json = #"{"id":1,"ticker":"AAPL","kind":"ma_cross","value":"50.0000","status":"active","note":null,"message":null,"created_at":"x","last_checked":null,"triggered_at":null}"#
        let a = try GhostlineJSON.decoder().decode(PriceAlert.self, from: Data(json.utf8))
        #expect(a.rule == "Crosses its 50-day average")
    }
}

@Suite struct InvestClientTests {
    @Test func aStatementFileCarriesBookMonthAndFormat() async throws {
        let (c, host) = fakeClient(answers: ["/statements": (200, "date,type\n")], tokens: InMemoryTokenStore(token: "sess"))
        let data = try await c.statementFile(book: "practice", month: "2026-10", format: "csv")
        #expect(String(decoding: data, as: UTF8.self) == "date,type\n")
        #expect(FakeServer.seen(host).first?.url?.query() == "book=practice&month=2026-10&format=csv")
    }
}
