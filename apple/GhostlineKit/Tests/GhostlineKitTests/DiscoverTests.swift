import Foundation
import Testing
@testable import GhostlineKit

@Suite struct DiscoverTests {
    @Test func moversDecodeLiveAndFromTheDailyFile() throws {
        let live = #"{"period":"day","direction":"gainers","source":"alpaca","through":"2026-10-30T23:59:00+00:00","since":null,"rows":[{"ticker":"UPUP","name":"Up Up Corp","kind":"CS","industry":"Technology","price":"101.0000","change_pct":"11.1000","dollar_volume":null,"volume":null}]}"#
        let m = try GhostlineJSON.decoder().decode(MoversList.self, from: Data(live.utf8))
        #expect(m.isLive && m.rows.first?.changePct == "11.1000" && m.rows.first?.industry == "Technology")
        let file = #"{"period":"week","direction":"active","source":"massive","through":"2026-10-30","since":"2026-10-23","rows":[{"ticker":"SMH","name":null,"kind":"ETF","industry":null,"price":"120.0000","change_pct":"9.09","dollar_volume":"600000000","volume":null}]}"#
        let w = try GhostlineJSON.decoder().decode(MoversList.self, from: Data(file.utf8))
        #expect(!w.isLive && w.since == "2026-10-23" && w.rows.first?.dollarVolume == "600000000")
    }

    @Test func sectorsIndustriesAndThemesDecode() throws {
        let s = try GhostlineJSON.decoder().decode(SectorList.self, from: Data(
            #"{"period":"week","through":"2026-10-30","rows":[{"name":"Technology","etf":"XLK","change_pct":"7.32"},{"name":"Utilities","etf":"XLU","change_pct":null}]}"#.utf8))
        #expect(s.rows.map(\.id) == ["XLK", "XLU"] && s.rows[1].changePct == nil)
        let i = try GhostlineJSON.decoder().decode(IndustryList.self, from: Data(
            #"{"period":"month","through":"2026-10-30","tagged":7,"rows":[{"name":"Banking","stocks":3,"change_pct":"10.00","best":"BANK"}]}"#.utf8))
        #expect(i.tagged == 7 && i.rows.first?.best == "BANK")
        let t = try GhostlineJSON.decoder().decode(ThemeList.self, from: Data(
            #"{"period":"week","through":"2026-10-30","themes":[{"id":"space","name":"Space","note":"Space funds.","etfs":[{"ticker":"UFO","name":null,"change_pct":"1.20"}],"industries":["Aerospace & Defense"],"leaders":[]}]}"#.utf8))
        #expect(t.themes.first?.etfs.first?.ticker == "UFO" && t.themes.first?.leaders.isEmpty == true)
    }
}
