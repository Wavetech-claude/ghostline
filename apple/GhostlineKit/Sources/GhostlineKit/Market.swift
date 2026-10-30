import Foundation

// Market data as the backend serves it: bars with their source and as-of time, indicator lines aligned to the bars,
// quotes that say they are delayed, news, earnings and filings.

public struct BarRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { ts }
    public let ts: String
    public let open: String
    public let high: String
    public let low: String
    public let close: String
    public let volume: String
    public let source: String
    public let asOf: String

    public var date: Date? { Stamp.tradingDay(ts) }    // a daily bar: its session's day
}

public struct Level: Codable, Sendable, Equatable, Identifiable {
    public var id: String { "\(kind)-\(price)" }
    public let price: Double
    public let kind: String
    public let touches: Int
}

public struct MACDLines: Codable, Sendable, Equatable {
    public let macd: [Double?]
    public let signal: [Double?]
    public let histogram: [Double?]
}

public struct BollingerLines: Codable, Sendable, Equatable {
    public let middle: [Double?]
    public let upper: [Double?]
    public let lower: [Double?]
}

public struct ChartData: Codable, Sendable, Equatable {
    public let ticker: String
    public let timeframe: String
    public let bars: [BarRow]
    public let source: String?
    public let asOf: String?
    public let sma20: [Double?]
    public let sma50: [Double?]
    public let sma200: [Double?]
    public let rsi14: [Double?]
    public let macd: MACDLines
    public let bollinger: BollingerLines
    public let levels: [Level]

    /// (date, value) pairs for one indicator line, skipping the bars where it has no value yet.
    public func line(_ values: [Double?]) -> [(Date, Double)] {
        zip(bars, values).compactMap { bar, v in
            guard let v, let d = bar.date else { return nil }
            return (d, v)
        }
    }
}

public struct QuoteRow: Codable, Sendable, Equatable {
    public let ticker: String
    public let bid: String?
    public let ask: String?
    public let last: String?
    public let ts: String
    public let source: String
    public let asOf: String
    public let delayed: Bool

    /// The price to show: the last trade, else the mid of bid and ask.
    public var shown: String? {
        if let last { return last }
        guard let bid, let ask, let b = Decimal(string: bid), let a = Decimal(string: ask) else { return nil }
        return "\((b + a) / 2)"
    }
}

public struct Quotes: Codable, Sendable, Equatable {
    public let quotes: [String: QuoteRow]
    public let missing: [String]
}

public struct NewsRow: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ticker: String?
    public let headline: String
    public let url: String
    public let publishedAt: String
    public let source: String
    public let tone: String?
}

public struct EarningsRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { "\(ticker)-\(at)" }
    public let ticker: String
    public let at: String
    public let estimate: String?
    public let actual: String?
    public let source: String
    public let asOf: String
    public let movePct: String?         // last close before the report to the first close after it
}

public struct FilingRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { accession }
    public let accession: String
    public let ticker: String
    public let form: String
    public let filedAt: String
    public let url: String?
    public let source: String
}

public struct NewsAndEarnings: Codable, Sendable, Equatable {
    public let ticker: String
    public let news: [NewsRow]
    public let earnings: [EarningsRow]
    public let filings: [FilingRow]
}

public struct WatchRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { ticker }
    public let ticker: String
    public let note: String?
    public let addedAt: String
    public let quote: QuoteRow?
}

public struct SourcePulls: Codable, Sendable, Equatable, Identifiable {
    public var id: String { source }
    public let source: String
    public let today: Int
    public let lastOk: String?
    public let lastCall: String?
}

public struct MarketStatus: Codable, Sendable, Equatable {
    public let configured: [String: Bool]
    public let pulls: [SourcePulls]
    public let tracked: [String]
}

public extension APIClient {
    func chart(ticker: String, timeframe: String = "6M") async throws -> ChartData {
        try await get("/charts", query: [URLQueryItem(name: "ticker", value: ticker),
                                        URLQueryItem(name: "timeframe", value: timeframe)], authenticated: true)
    }

    func quotes(_ tickers: [String]) async throws -> Quotes {
        try await get("/quotes", query: [URLQueryItem(name: "tickers", value: tickers.joined(separator: ","))],
                      authenticated: true)
    }

    func newsAndEarnings(ticker: String) async throws -> NewsAndEarnings {
        try await get("/news-earnings", query: [URLQueryItem(name: "ticker", value: ticker)], authenticated: true)
    }

    func watchlist() async throws -> [WatchRow] {
        struct Answer: Decodable { let watchlist: [WatchRow] }
        return try await (get("/watchlist", authenticated: true) as Answer).watchlist
    }

    func watch(_ ticker: String, note: String? = nil) async throws {
        struct Body: Encodable { let ticker: String; let note: String? }
        struct Ack: Decodable { let ok: Bool }
        _ = try await post("/watchlist", body: Body(ticker: ticker, note: note), authenticated: true) as Ack
    }

    func unwatch(_ ticker: String) async throws {
        struct Ack: Decodable { let ok: Bool }
        var r = try request("/watchlist/\(ticker)", method: "DELETE", authenticated: true)
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        _ = try await send(r) as Ack
    }

    func marketStatus() async throws -> MarketStatus {
        try await get("/market/status", authenticated: true)
    }
}
