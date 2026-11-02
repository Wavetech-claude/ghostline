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
    public let timeframe: String?          // "1D", or an intraday bar length: "5Min", "30Min"

    /// A bar shorter than a day (the 1D and 1W charts).
    public var isIntraday: Bool { timeframe.map { $0.hasSuffix("Min") || $0.hasSuffix("Hour") } ?? false }
    /// The moment the bar starts.
    public var instant: Date? { Stamp.date(ts) }
    /// A daily bar: its session's day. An intraday bar: the moment it starts.
    public var date: Date? { isIntraday ? instant : Stamp.tradingDay(ts) }
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
    public let interval: String?           // "1Day", "5Min" (1D) or "30Min" (1W)
    public let feed: String?               // "iex": live, IEX's share of the volume
    public let delayed: Bool?
    public let previousClose: String?      // intraday: the close before the latest session (the 1D baseline)
    public let marketOpen: Bool?

    public var isIntraday: Bool { interval.map { $0.hasSuffix("Min") || $0.hasSuffix("Hour") } ?? false }

    /// Where each bar sits on the x axis. Daily bars: their day. Intraday bars: their moment, with the closed hours
    /// between sessions taken out, so a week of 30-minute bars draws as one line instead of five islands separated by
    /// nights and a weekend. The axis is hidden or labelled from the bars' own times wherever these are used.
    public var plotDates: [Date?] {
        guard isIntraday else { return bars.map(\.date) }
        var out: [Date?] = []
        var last: (real: Date, plot: Date)?
        let step = Self.seconds(of: interval ?? "") ?? 300
        for bar in bars {
            guard let real = bar.instant else { out.append(nil); continue }
            let plot = last.map { $0.plot.addingTimeInterval(min(real.timeIntervalSince($0.real), step)) } ?? real
            out.append(plot)
            last = (real, plot)
        }
        return out
    }

    /// The bar whose plotted x is nearest `x` (a finger dragging across the chart).
    public func bar(nearest x: Date) -> BarRow? {
        let plotted = zip(bars, plotDates).compactMap { bar, d in d.map { (bar, $0) } }
        return plotted.min { abs($0.1.timeIntervalSince(x)) < abs($1.1.timeIntervalSince(x)) }?.0
    }

    /// The plotted x of a bar.
    public func plotDate(of bar: BarRow) -> Date? {
        guard let i = bars.firstIndex(where: { $0.ts == bar.ts }) else { return nil }
        return plotDates[i]
    }

    static func seconds(of interval: String) -> TimeInterval? {
        if interval.hasSuffix("Min"), let n = Double(interval.dropLast(3)) { return n * 60 }
        if interval.hasSuffix("Hour"), let n = Double(interval.dropLast(4)) { return n * 3600 }
        return nil
    }

    /// (x, value) pairs for one indicator line, skipping the bars where it has no value yet.
    public func line(_ values: [Double?]) -> [(Date, Double)] {
        zip(plotDates, values).compactMap { d, v in
            guard let v, let d else { return nil }
            return (d, v)
        }
    }

    /// The line under the chart's title: where the bars come from and how fresh they are.
    public var sourceLine: String {
        let src = source.map { $0 == "alpaca" ? "Alpaca" : $0 } ?? "?"
        guard isIntraday else { return "\(src) · daily bars · as of \(Stamp.short(asOf))" }
        let length = interval.map { $0.replacingOccurrences(of: "Min", with: "-min").replacingOccurrences(of: "Hour", with: "-hour") } ?? ""
        let feedName = (feed ?? "").uppercased()
        let time = asOf.flatMap(Stamp.date).map { $0.formatted(date: .omitted, time: .shortened) } ?? "?"
        return "\(src) \(feedName) · \(length) bars · as of \(time)" + (marketOpen == true ? " · market open" : "")
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
    public var hour: String? = nil      // bmo (before the open), amc (after the close), dmh (during the session)
    public var revenueEstimate: String? = nil
    public var revenueActual: String? = nil
}

/// When in the day a company reports, in words.
public enum ReportHour {
    public static func words(_ hour: String?) -> String? {
        switch hour {
        case "bmo": "Before the open"
        case "amc": "After the close"
        case "dmh": "During the session"
        default: nil
        }
    }
}

/// One report on the earnings calendar: a stock the owner holds, watches or has an open idea on.
public struct CalendarEarning: Codable, Sendable, Equatable, Identifiable {
    public var id: String { "\(ticker)-\(at)" }
    public let ticker: String
    public let name: String?
    public let at: String                  // the report's day, yyyy-MM-dd
    public let hour: String?
    public let estimate: String?
    public let actual: String?
    public let revenueEstimate: String?
    public let revenueActual: String?
    public let surprisePct: String?        // actual against the estimate, in percent
    public let why: [String]               // held, watched, idea
    public let movePct: String?            // a past report: how the stock moved around it
    public let typicalMovePct: String?     // a coming report: the average size of the moves around the last four
}

public struct EarningsCalendar: Codable, Sendable, Equatable {
    public let from: String
    public let until: String
    public let earnings: [CalendarEarning]

    /// The reports grouped by day, in date order.
    public var days: [(day: String, reports: [CalendarEarning])] {
        Dictionary(grouping: earnings, by: \.at).sorted { $0.key < $1.key }.map { (day: $0.key, reports: $0.value) }
    }
}

/// One month of Finnhub's recommendation trends: how many analysts say each grade.
public struct AnalystTrend: Codable, Sendable, Equatable, Identifiable {
    public var id: String { period }
    public let period: String
    public let strongBuy: Int
    public let buy: Int
    public let hold: Int
    public let sell: Int
    public let strongSell: Int
    public var total: Int { strongBuy + buy + hold + sell + strongSell }
}

/// FMP's grade consensus and price targets.
public struct AnalystConsensus: Codable, Sendable, Equatable {
    public let strongBuy: Int?
    public let buy: Int?
    public let hold: Int?
    public let sell: Int?
    public let strongSell: Int?
    public let consensus: String?
    public let targetHigh: String?
    public let targetLow: String?
    public let targetConsensus: String?
    public let targetMedian: String?
    public let source: String
    public let asOf: String
}

public struct AnalystViews: Codable, Sendable, Equatable {
    public let ticker: String
    public let trends: [AnalystTrend]
    public let consensus: AnalystConsensus?

    public var isEmpty: Bool { trends.isEmpty && consensus == nil }
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

    /// Reports of what the owner holds, watches or has an open idea on (GET /earnings/calendar).
    func earningsCalendar(days: Int = 30) async throws -> EarningsCalendar {
        try await get("/earnings/calendar", query: [URLQueryItem(name: "days", value: String(days))], authenticated: true)
    }

    /// Analysts' recommendation trends, grade consensus and price targets for one stock (GET /analysts).
    func analysts(ticker: String) async throws -> AnalystViews {
        try await get("/analysts", query: [URLQueryItem(name: "ticker", value: ticker)], authenticated: true)
    }

    func newsAndEarnings(ticker: String) async throws -> NewsAndEarnings {
        try await get("/news-earnings", query: [URLQueryItem(name: "ticker", value: ticker)], authenticated: true)
    }

    /// Search as you type (GET /market/search): a ticker prefix or a word of the name; held and watched first, then the
    /// exact ticker, then the most traded. Warrants, rights and units only with `all`.
    func searchTickers(_ query: String, limit: Int = 20, all: Bool = false) async throws -> [TickerMatch] {
        var items = [URLQueryItem(name: "q", value: query), URLQueryItem(name: "limit", value: String(limit))]
        if all { items.append(URLQueryItem(name: "all", value: "true")) }
        return try await (get("/market/search", query: items, authenticated: true) as TickerMatches).results
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

/// One search result: a listed stock or fund, whether the owner watches or holds it, and what it is (Massive's type:
/// CS common stock, ETF, ADRC, ...; nil when no list typed it).
public struct TickerMatch: Codable, Sendable, Equatable, Identifiable {
    public let ticker: String
    public let name: String
    public let watched: Bool
    public let held: Bool?
    public let kind: String?
    public var id: String { ticker }

    public init(ticker: String, name: String, watched: Bool, held: Bool? = nil, kind: String? = nil) {
        self.ticker = ticker
        self.name = name
        self.watched = watched
        self.held = held
        self.kind = kind
    }

    /// "ETF" for a fund, nothing for a common stock.
    public var badge: String? {
        switch kind {
        case "ETF", "ETN", "ETV", "ETS", "FUND": "ETF"
        case "ADRC": "ADR"
        case "PFD": "Preferred"
        case "WARRANT": "Warrant"
        case "RIGHT": "Right"
        case "UNIT": "Unit"
        default: nil
        }
    }
}

public struct TickerMatches: Codable, Sendable, Equatable {
    public let results: [TickerMatch]
}

