import Foundation
import Testing
@testable import GhostlineKit

@Suite struct CorrectionsModelTests {
    @Test func decodesAVoidedTradeAndHistoryWithItsVoidedList() throws {
        let json = #"""
        {"closed":[],"open":[],"totals":{"trades":0,"win_rate":null,"average_win":null,"average_loss":null,"net_pnl":"0.00"},
         "voided":[{"id":7,"book":"real","ticker":"MSFT","asset":"stock","side":"buy","quantity":"3","fill":"50.0000",
                    "at":"2026-10-07T14:30:00+00:00","fees":"0.00","broker":null,"note":null,"option_symbol":null,"lane":null,
                    "idea_id":null,"memo_id":null,"attachments":0,"voided_at":"2026-11-01T20:00:00+00:00","void_reason":"never bought"}]}
        """#
        let h = try GhostlineJSON.decoder().decode(History.self, from: Data(json.utf8))
        let t = try #require(h.voided?.first)
        #expect(t.isVoided && t.voidReason == "never bought" && !t.isEditable)
        let plain = #"{"closed":[],"open":[],"totals":{"trades":0,"win_rate":null,"average_win":null,"average_loss":null,"net_pnl":"0.00"}}"#
        #expect(try GhostlineJSON.decoder().decode(History.self, from: Data(plain.utf8)).voided == nil)
    }

    @Test func onlyAnOwnerTypedLiveRealTradeIsEditable() throws {
        func row(_ book: String, _ side: String, _ broker: String?) throws -> TradeRow {
            let b = broker.map { "\"\($0)\"" } ?? "null"
            let json = #"{"id":1,"book":"\#(book)","ticker":"A","asset":"stock","side":"\#(side)","quantity":"1","fill":"1","at":"x","fees":"0","broker":\#(b),"note":null,"option_symbol":null,"lane":null,"idea_id":null,"memo_id":null,"attachments":0,"voided_at":null,"void_reason":null}"#
            return try GhostlineJSON.decoder().decode(TradeRow.self, from: Data(json.utf8))
        }
        #expect(try row("real", "buy", "Robinhood").isEditable)
        #expect(try !row("practice", "buy", "practice").isEditable)
        #expect(try !row("real", "split", "corporate action").isEditable)
        #expect(try !row("real", "sell", "corporate action").isEditable)
    }

    @Test func decodesATradeDetailWithItsEditsAndACashFlowDetail() throws {
        let json = #"""
        {"id":3,"book":"real","ticker":"MSFT","asset":"stock","side":"buy","quantity":"12","fill":"101.5000","at":"x","fees":"0.00",
         "broker":"Fidelity","note":null,"lane":"swing","option_symbol":null,"voided_at":null,"void_reason":null,"attachment_list":[],
         "edits":[{"action":"edit","field":"quantity","old":"10","new":"12","reason":null,"by_whom":"owner","at":"2026-11-01T20:00:00+00:00"},
                  {"action":"void","field":null,"old":null,"new":null,"reason":"typo","by_whom":"owner","at":"2026-11-01T20:01:00+00:00"}]}
        """#
        let d = try GhostlineJSON.decoder().decode(TradeDetail.self, from: Data(json.utf8))
        #expect(d.edits?.count == 2 && d.edits?.first?.new == "12" && d.edits?.last?.reason == "typo")
        let flow = #"{"id":2,"book":"real","amount":"450.00","at":"x","note":"typo","voided_at":null,"void_reason":null,"created_at":"x","edits":[]}"#
        #expect(try GhostlineJSON.decoder().decode(CashFlowDetail.self, from: Data(flow.utf8)).amount == "450.00")
    }

    @Test func tradeChangesSendOnlyWhatChangedAndClearOnRequest() throws {
        var c = TradeChanges()
        #expect(c.isEmpty)
        c.quantity = "12"
        c.optionSymbol = "MSFT261121P00100000"
        c.clear = ["note"]
        let json = try JSONSerialization.jsonObject(with: GhostlineJSON.encoder().encode(c)) as? [String: Any]
        #expect(json?["quantity"] as? String == "12" && json?["option_symbol"] as? String == "MSFT261121P00100000")
        #expect(json?["note"] is NSNull && json?["fill"] == nil && json?.count == 3)
    }

    @Test func aReplaceableOrderIsAnOpenLimitOrStop() throws {
        func order(_ type: String, _ status: String) throws -> OrderRow {
            let json = #"{"id":1,"ticker":"A","asset":"stock","side":"buy","type":"\#(type)","tif":"day","quantity":"1","status":"\#(status)","triggered":false,"placed_at":"x","replaces_order_id":4}"#
            return try GhostlineJSON.decoder().decode(OrderRow.self, from: Data(json.utf8))
        }
        #expect(try order("limit", "open").isReplaceable && order("stop_limit", "open").replacesOrderId == 4)
        #expect(try !order("market", "open").isReplaceable && !order("limit", "filled").isReplaceable)
    }
}

@Suite struct CorrectionsClientTests {
    @Test func editIsAPatchAndVoidAPost() async throws {
        let ack = #"{"id":5}"#
        let (c, host) = fakeClient(answers: ["/trades/5": (200, ack), "/trades/5/void": (200, ack)],
                                   tokens: InMemoryTokenStore(token: "sess"))
        var changes = TradeChanges()
        changes.fees = "1"
        try await c.editTrade(id: 5, changes)
        try await c.voidTrade(id: 5, reason: "typo")
        let seen = FakeServer.seen(host)
        #expect(seen.map(\.httpMethod) == ["PATCH", "POST"])
        #expect(seen.last?.url?.path() == "/trades/5/void")
    }

    @Test func resetPostsTheBookAndDecodesTheResult() async throws {
        let answer = #"{"book":"practice","reason":"reset 2026-11-01","trades":2,"cash_flows":1,"orders_cancelled":1,"deposit_id":null}"#
        let (c, host) = fakeClient(answers: ["/practice/reset": (200, answer)], tokens: InMemoryTokenStore(token: "sess"))
        let r = try await c.resetBook("practice")
        #expect(r.trades == 2 && r.cashFlows == 1 && r.ordersCancelled == 1 && r.depositId == nil)
        #expect(FakeServer.seen(host).first?.httpMethod == "POST")
    }
}
