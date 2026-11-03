#if DEBUG
import Foundation
import GhostlineKit
import Synchronization
import SwiftUI

/// UI tests only, in Debug builds: `-uiTestScreen <name>` opens one real screen with the backend replaced by fixed
/// answers, so the tests tap the real views with no server and no sign-in. A Release build has none of this code.
enum UITestFixture {
    static let screen: String? = {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-uiTestScreen"), args.indices.contains(i + 1) else { return nil }
        return args[i + 1]
    }()

    @MainActor static func session() -> SessionModel {
        let base = URL(string: "https://fixture.invalid")!
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [FixtureProtocol.self]
        let client = APIClient(baseURL: base, tokens: InMemoryTokenStore(token: "fixture"),
                               session: URLSession(configuration: config))
        return SessionModel(client: client, configuration: AppConfiguration(baseURL: base, environment: "fixture"))
    }
}

/// The screen under test, presented the way the app presents it: the watchlist and search in a navigation stack, the
/// forms as sheets (so the preview opens over a sheet, as it does in the app).
struct UITestHost: View {
    @Environment(PracticeModel.self) private var practice
    let screen: String
    @State private var sheet = false

    var body: some View {
        @Bindable var practice = practice
        NavigationStack {
            switch screen {
            case "watchlist": WatchlistView()
            case "search": SearchView()
            default: Button("Open \(screen)") { sheet = true }.task { sheet = true }
            }
        }
        .sheet(isPresented: $sheet) {
            switch screen {
            case "alert": AddAlertSheet(ticker: "")
            case "order": OrderTicketView()
            case "trade": TradeFormView()
            case "statement": StatementSheet(saved: {})
            default: Text("No screen named \(screen)")
            }
        }
        .sheet(isPresented: $practice.showingTicket) { OrderTicketView() }
    }
}

/// Fixed answers for the calls the screens under test make; any other call answers 404 with its path.
final class FixtureProtocol: URLProtocol, @unchecked Sendable {
    static let watched = Mutex<[String]>(["NVDA"])

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        guard let url = request.url else { return }
        let (code, body) = Self.answer(method: request.httpMethod ?? "GET", url: url, body: Self.body(of: request))
        let response = HTTPURLResponse(url: url, statusCode: code, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    private static func body(of request: URLRequest) -> Data {
        if let data = request.httpBody { return data }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let n = stream.read(&buffer, maxLength: buffer.count)
            if n <= 0 { break }
            data.append(buffer, count: n)
        }
        return data
    }

    static let stocks = [("NFLX", "Netflix Inc.", true), ("NVDA", "NVIDIA Corp", false)]   // ticker, name, held

    static func answer(method: String, url: URL, body: Data) -> (Int, String) {
        let path = url.path()
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let watching = watched.withLock { $0 }
        switch (method, path) {
        case ("GET", "/version"):
            return (200, #"{"version": "0.0.0", "commit": "fixture", "env": "fixture"}"#)
        case ("GET", "/market/search"):
            let q = (query.first { $0.name == "q" }?.value ?? "").lowercased()
            let rows = stocks.filter { t, name, _ in
                t.lowercased().hasPrefix(q) || name.lowercased().split(separator: " ").contains { $0.hasPrefix(q) }
            }.map { t, name, held in
                #"{"ticker": "\#(t)", "name": "\#(name)", "watched": \#(watching.contains(t)), "held": \#(held), "kind": "CS"}"#
            }
            return (200, #"{"results": [\#(rows.joined(separator: ", "))]}"#)
        case ("GET", "/charts"):
            return (200, chart(query.first { $0.name == "ticker" }?.value ?? ""))
        case ("GET", "/portfolio"):
            return (200, portfolio)
        case ("GET", "/watchlist"):
            let rows = watching.map { #"{"ticker": "\#($0)", "note": null, "added_at": "2026-11-02T08:00:00Z", "quote": null}"# }
            return (200, #"{"watchlist": [\#(rows.joined(separator: ", "))]}"#)
        case ("POST", "/watchlist"):
            struct Body: Decodable { let ticker: String }
            guard let t = try? JSONDecoder().decode(Body.self, from: body).ticker else { return (422, #"{"detail": "no ticker"}"#) }
            watched.withLock { if !$0.contains(t) { $0.append(t) } }
            return (200, #"{"ok": true}"#)
        case ("DELETE", _) where path.hasPrefix("/watchlist/"):
            let t = String(path.dropFirst("/watchlist/".count))
            watched.withLock { $0.removeAll { $0 == t } }
            return (200, #"{"ok": true}"#)
        default:
            return (404, #"{"detail": "no fixture for \#(method) \#(path)"}"#)
        }
    }

    /// A 1D chart: three 5-minute bars of Friday's session, closing at 110.50 against a previous close of 100.00.
    static func chart(_ ticker: String) -> String {
        let bars = [("13:30", "100.50", "104.20"), ("13:35", "104.20", "108.90"), ("13:40", "108.90", "110.50")]
            .map { time, open, close in
                #"{"ts": "2026-10-30T\#(time):00Z", "open": "\#(open)", "high": "\#(close)", "low": "\#(open)", "close": "\#(close)", "volume": "1000", "source": "alpaca", "as_of": "2026-10-30T20:00:00Z", "timeframe": "5Min"}"#
            }
        let empty = "[null, null, null]"
        return #"""
        {"ticker": "\#(ticker)", "timeframe": "1D", "interval": "5Min", "feed": "iex", "delayed": false,
         "bars": [\#(bars.joined(separator: ", "))], "source": "alpaca", "as_of": "2026-10-30T20:00:00Z",
         "previous_close": "100.00", "market_open": false,
         "sma20": \#(empty), "sma50": \#(empty), "sma200": \#(empty), "rsi14": \#(empty),
         "macd": {"macd": \#(empty), "signal": \#(empty), "histogram": \#(empty)},
         "bollinger": {"middle": \#(empty), "upper": \#(empty), "lower": \#(empty)}, "levels": []}
        """#
    }

    static let portfolio = #"""
    {"books": [{"book": "real", "name": "Real", "cash": "0", "invested": "270", "total": "270", "priced": true,
                "net_deposits": "270", "total_pnl": null, "day_pnl": null, "day_pnl_partial": null,
                "positions": [{"ticker": "NFLX", "quantity": "3", "average_cost": "90", "cost_basis": "270",
                               "price": null, "price_source": null, "price_as_of": null, "value": null,
                               "unrealized_pnl": null, "day_pnl": null, "lane": null}],
                "allocation": []}],
     "total": "270", "note": null}
    """#
}
#endif
