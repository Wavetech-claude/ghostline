import Foundation
import Testing
@testable import GhostlineKit

@Suite struct ReconcileTests {
    @Test func aMonthsReportDecodes() throws {
        let json = #"""
        {"month":"2026-10","suggestions":[{"kind":"idea","id":7,"ticker":"AAA","label":"swing","at":"2026-10-30T13:20:00+00:00","outcome":"took","reason":null,"suggested":{"entry":"100.0000","quantity":"10.000000"},"taken":{"trade_id":3,"quantity":"10.000000","fill":"100.0000","at":"2026-10-30T14:00:00+00:00"},"paper":{"status":"settled","result_pct":"1.20","excess_pct":"-1.00"},"realized":"100.00"},{"kind":"memo","id":7,"ticker":"MMM","label":"buy call","at":"2026-10-30T15:00:00+00:00","outcome":"declined","reason":"Too close to earnings","suggested":{"entry":null,"quantity":null},"taken":null,"paper":null,"realized":null}],
         "own_calls":[{"trade_id":9,"ticker":"ZZZ","side":"buy","quantity":"5.000000","fill":"20.0000","at":"2026-10-30T16:00:00+00:00","realized":null}],
         "summary":{"took":{"n":1,"paper_settled":1,"paper_excess_avg":"-1.00","realized":"100.00"},"declined":{"n":1,"paper_settled":0,"paper_excess_avg":null,"realized":null},"lapsed":{"n":0,"paper_settled":0,"paper_excess_avg":null,"realized":null},"open":{"n":0,"paper_settled":0,"paper_excess_avg":null,"realized":null},"own_calls":{"n":1,"realized":null}},
         "broker":{"statement_id":2,"as_of":"2026-10-21","shares":[{"ticker":"MSFT","app":"3","broker":"0","difference":"-3","match":false}],"cash":{"app":"1800.00","broker":"1700.00","difference":"-100.00","match":false},"matches":false,"note":"Stock shares and cash are compared; options are not compared yet."}}
        """#
        let r = try GhostlineJSON.decoder().decode(Reconciliation.self, from: Data(json.utf8))
        #expect(r.suggestions.map(\.key) == ["idea-7", "memo-7"])          // one id, two kinds, two rows
        #expect(r.suggestions[0].taken?.tradeId == 3 && r.suggestions[0].paper?.excessPct == "-1.00")
        #expect(r.ownCalls.first?.id == 9 && r.summary.ownCalls.paperSettled == nil)
        #expect(r.broker?.shares.first?.difference == "-3" && r.broker?.cash.match == false)
    }

    @Test func aTradeCarriesTheIdeaItFollows() throws {
        let input = TradeInput(book: "real", ticker: "AAA", side: "buy", quantity: "10", fill: "100", at: "2026-10-30T14:00:00Z",
                               ideaId: 7)
        let body = try #require(String(data: try GhostlineJSON.encoder().encode(input), encoding: .utf8))
        #expect(body.contains(#""idea_id":7"#) && !body.contains("memo_id"))
    }

    @Test func pendingCorporateActionsDecode() throws {
        let json = #"{"pending":[{"id":"div-1","kind":"cash_dividend","ticker":"NVDA","ex_date":"2024-06-11","payable_date":"2024-06-28","shares":"50","amount":"0.50","new_rate":null,"old_rate":null,"text":"NVDA dividend: 0.50 (0.01 a share on 50)"}]}"#
        let p = try GhostlineJSON.decoder().decode(PendingActions.self, from: Data(json.utf8))
        #expect(p.pending.first?.isDividend == true && p.pending.first?.payableDate == "2024-06-28")
        #expect(p.pending.first?.amount == "0.50" && p.pending.first?.newRate == nil)
    }
}
