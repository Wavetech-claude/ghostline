import Foundation
import Testing
@testable import GhostlineKit

@Suite struct PracticeModelTests {
    @Test func decodesARefusedOrderWithItsReason() throws {
        let json = #"""
        {"id":4,"book_id":2,"ticker":"AAPL","asset":"stock","side":"buy","type":"market","tif":"day","quantity":"5","limit_price":null,
         "stop_price":null,"stop_loss":null,"option_symbol":null,"status":"refused","reason":"max_loss_per_idea_share: max loss 500.50 exceeds 0.015000 of the balance 1000.00",
         "triggered":false,"placed_at":"2026-10-30T10:00:00+00:00","filled_at":null,"fill_price":null,"trade_id":null,"note":null}
        """#
        let o = try GhostlineJSON.decoder().decode(OrderRow.self, from: Data(json.utf8))
        #expect(o.isRefused && o.reason?.hasPrefix("max_loss_per_idea_share") == true && o.book == nil && o.fillPrice == nil)
    }

    @Test func encodesAnOrderInSnakeCaseWithoutEmptyFields() throws {
        let i = OrderInput(ticker: "AAPL", side: "buy", type: "limit", tif: "gtc", quantity: "1.5", limitPrice: "90", stopLoss: "85")
        let json = try JSONSerialization.jsonObject(with: GhostlineJSON.encoder().encode(i)) as? [String: Any]
        #expect(json?["limit_price"] as? String == "90" && json?["stop_loss"] as? String == "85" && json?["book"] as? String == "practice")
        #expect(json?["stop_price"] == nil || json?["stop_price"] is NSNull)
    }

    @Test func decodesTheChainAndSplitsPutsFromCalls() throws {
        let json = #"""
        {"ticker":"X","expiry":"2026-11-20","configured":true,"reason":null,"source":"tradier","as_of":"2026-10-30T10:00:00+00:00",
         "available":"1500.00","book":"practice","options":[
           {"option_symbol":"X261120P00010000","underlying":"X","expiry":"2026-11-20","strike":"10","kind":"put","bid":"0.5","ask":"0.6","last":null,"volume":"120","open_interest":"800","iv":"0.42","delta":"-0.3","collateral":"1000","covered":true},
           {"option_symbol":"X261120C00010000","underlying":"X","expiry":"2026-11-20","strike":"10","kind":"call","bid":"1.0","ask":"1.2","last":null,"volume":null,"open_interest":"300","iv":null,"delta":null,"collateral":null,"covered":false}]}
        """#
        let c = try GhostlineJSON.decoder().decode(OptionChain.self, from: Data(json.utf8))
        #expect(c.puts.count == 1 && c.calls.count == 1 && c.puts[0].covered && c.puts[0].collateral == "1000")
    }
}

@Suite struct PracticeClientTests {
    @Test func cancelIsADeleteOnTheOrder() async throws {
        let row = #"{"id":9,"ticker":"AAPL","asset":"stock","side":"buy","type":"limit","tif":"gtc","quantity":"1","status":"cancelled","reason":"cancelled by the owner","triggered":false,"placed_at":"x"}"#
        let (c, host) = fakeClient(answers: ["/practice/orders/9": (200, row)], tokens: InMemoryTokenStore(token: "sess"))
        let o = try await c.cancelOrder(id: 9)
        #expect(o.status == "cancelled")
        #expect(FakeServer.seen(host).first?.httpMethod == "DELETE")
    }

    @Test func chainCarriesTickerExpiryAndBook() async throws {
        let empty = #"{"ticker":"X","expiry":"2026-11-20","configured":false,"reason":"Tradier is not configured","source":"tradier","as_of":null,"available":"0.00","book":"practice","options":[]}"#
        let (c, host) = fakeClient(answers: ["/options/chain": (200, empty)], tokens: InMemoryTokenStore(token: "sess"))
        let ch = try await c.chain(ticker: "X", expiry: "2026-11-20")
        #expect(!ch.configured && ch.options.isEmpty)
        #expect(FakeServer.seen(host).first?.url?.query() == "ticker=X&expiry=2026-11-20&book=practice")
    }
}

@Suite struct IdeasModelTests {
    @Test func decodesIdeasWithARefusedOneAndTheEvidence() throws {
        let json = #"""
        {"ideas":[{"id":1,"lane":"swing","ticker":"MSFT","signal_id":3,"module":"vcp","status":"refused","book":"practice","balance":"10000.00",
          "entry":"50.0000","stop":"48.0000","target":"60.0000","quantity":"20","size_share":"0.1000","max_loss":"40.00","collateral":null,"option_symbol":null,
          "refusal":"earnings_blackout_days: the earnings date is unknown","opens_at":"2026-10-30T10:00:00+00:00","expires_at":"2026-12-09T10:00:00+00:00",
          "score":"80.00","reason":"MSFT: a tight base","horizon_days":40,"as_of":"2026-10-30T09:00:00+00:00","source":"test","direction":"long"}],
         "counts":[{"lane":"swing","status":"refused","n":1}],"last_scan":{"day":"2026-10-30","signals":7}}
        """#
        let i = try GhostlineJSON.decoder().decode(Ideas.self, from: Data(json.utf8))
        #expect(i.ideas[0].refusal?.hasPrefix("earnings_blackout_days") == true && !i.ideas[0].isOpen && i.lastScan.signals == 7)
        let sig = #"{"id":3,"ticker":"MSFT","module":"vcp","lane":"swing","direction":"long","score":"80.00","actionable":true,"horizon_days":40,"entry":"50","stop":"48","target":"60","reason":"r","evidence":{"state":"pre_breakout","pivot":50.25,"checks":{"a":true},"ok":true},"day":"2026-10-30","source":"test","as_of":"x"}"#
        let s = try GhostlineJSON.decoder().decode(SignalRow.self, from: Data(sig.utf8))
        let lines = s.evidence.lines
        #expect(lines.map(\.0) == ["checks", "ok", "pivot", "state"])
        #expect(lines.map(\.1) == ["1 fields", "yes", "50.25", "pre_breakout"])
    }
}
