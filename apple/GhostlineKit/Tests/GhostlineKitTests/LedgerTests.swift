import Foundation
import Testing
@testable import GhostlineKit

@Suite struct LedgerModelTests {
    @Test func decodesTheHistoryAnswer() throws {
        let json = #"""
        {"closed":[{"trade_id":3,"book":"real","ticker":"AAPL","asset":"stock","lane":"swing","quantity":"15","at":"2026-10-03T14:30:00+00:00","proceeds":"1798.50","cost":"1551.50","pnl":"247.00","pnl_pct":"15.92","opened_trade_ids":[1,2],"idea_id":null,"memo_id":null,"attachments":0}],
         "open":[{"book":"real","ticker":"AAPL","quantity":"5","average_cost":"110.10","cost_basis":"550.50","price":null,"price_source":null,"price_as_of":null,"unrealized_pnl":null}],
         "totals":{"trades":1,"win_rate":"100.00","average_win":"247.00","average_loss":null,"net_pnl":"247.00"}}
        """#
        let h = try GhostlineJSON.decoder().decode(History.self, from: Data(json.utf8))
        #expect(h.closed[0].pnl == "247.00" && h.closed[0].openedTradeIds == [1, 2])
        #expect(h.open[0].price == nil && h.open[0].averageCost == "110.10")
        #expect(h.totals.averageLoss == nil && h.totals.netPnl == "247.00")
    }

    @Test func encodesATradeInSnakeCase() throws {
        let t = TradeInput(book: "real", ticker: "AAPL", side: "buy", quantity: "0.5", fill: "200",
                           at: "2026-10-03T14:30:00Z", fees: "1", broker: "Robinhood", lane: "long")
        let json = try JSONSerialization.jsonObject(with: GhostlineJSON.encoder().encode(t)) as? [String: Any]
        #expect(json?["option_symbol"] == nil || json?["option_symbol"] is NSNull)
        #expect(json?["quantity"] as? String == "0.5" && json?["book"] as? String == "real")
    }

    @Test func filtersBecomeQueryItemsAndSkipEmptyOnes() {
        let f = TradeFilters(book: "real", ticker: "", result: "win")
        #expect(f.queryItems == [URLQueryItem(name: "book", value: "real"), URLQueryItem(name: "result", value: "win")])
    }

    @Test func stampsRoundTrip() {
        let d = Date(timeIntervalSince1970: 1_790_000_000)
        #expect(Stamp.date(Stamp.string(d)) == d)
        #expect(Stamp.date("2026-10-30T09:00:00.123456+00:00") != nil)
        #expect(Stamp.short(nil) == "—")
    }

    @Test func rulesDecodeWithMissingValues() throws {
        let json = #"{"rules":[{"key":"max_open_ideas","kind":"count","suggested":"5","label":"Cap","value":null,"enabled":null,"updated_at":null}],"missing":["max_open_ideas"],"complete":false}"#
        let r = try GhostlineJSON.decoder().decode(RiskRules.self, from: Data(json.utf8))
        #expect(!r.complete && r.rules[0].value == nil && r.rules[0].suggested == "5")
    }
}

@Suite struct LedgerClientTests {
    private func client(answers: [String: (Int, String)]) -> (APIClient, String) {
        fakeClient(answers: answers, tokens: InMemoryTokenStore(token: "sess"))
    }

    @Test func historyCarriesItsFilters() async throws {
        let (c, host) = client(answers: ["/history": (200, #"{"closed":[],"open":[],"totals":{"trades":0,"win_rate":null,"average_win":null,"average_loss":null,"net_pnl":"0.00"}}"#)])
        _ = try await c.history(TradeFilters(book: "practice", lane: "puts"))
        #expect(FakeServer.seen(host).first?.url?.query() == "book=practice&lane=puts")
    }

    @Test func uploadIsMultipartWithTheTradeId() async throws {
        let (c, host) = client(answers: ["/attachments": (201, #"{"id":7,"trade_id":3,"content_type":"image/png","bytes":4,"filename":"c.png","created_at":"x"}"#)])
        let a = try await c.uploadAttachment(tradeId: 3, data: Data([1, 2, 3, 4]), filename: "c.png", contentType: "image/png")
        #expect(a.id == 7)
        let req = try #require(FakeServer.seen(host).first)
        #expect(req.url?.query() == "trade_id=3")
        #expect(req.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
        #expect(req.httpMethod == "POST")
    }

    @Test func rulesAreSavedWithPut() async throws {
        let (c, host) = client(answers: ["/risk-rules": (200, #"{"rules":[],"missing":[],"complete":true}"#)])
        let r = try await c.saveRiskRules([RuleInput(key: "max_open_ideas", value: "5", enabled: true)])
        #expect(r.complete && FakeServer.seen(host).first?.httpMethod == "PUT")
    }
}
