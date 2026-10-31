import Foundation

// The ledger's answers. Money and quantities arrive as decimal strings and stay strings until shown; `Decimal(string:)`
// turns them into numbers for arithmetic, never Double.

public struct BookSummary: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let kind: String
    public let name: String
    public let deposits: String
    public let withdrawals: String
    public let cash: String
    public let trades: Int
}

public struct CashFlowRow: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let book: String
    public let amount: String
    public let at: String
    public let note: String?
}

public struct CashFlowInput: Codable, Sendable, Equatable {
    public let book: String
    public let amount: String
    public let at: String
    public let note: String?

    public init(book: String, amount: String, at: String, note: String?) {
        self.book = book
        self.amount = amount
        self.at = at
        self.note = note
    }
}

public struct TradeRow: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let book: String
    public let ticker: String
    public let asset: String
    public let side: String
    public let quantity: String
    public let fill: String
    public let at: String
    public let fees: String
    public let broker: String?
    public let note: String?
    public let optionSymbol: String?
    public let lane: String?
    public let ideaId: Int?
    public let memoId: Int?
    public let attachments: Int
}

public struct TradeInput: Codable, Sendable, Equatable {
    public var book: String
    public var ticker: String
    public var asset: String
    public var side: String
    public var quantity: String
    public var fill: String
    public var at: String
    public var fees: String
    public var broker: String?
    public var note: String?
    public var optionSymbol: String?
    public var lane: String?
    public var ideaId: Int?             // the suggestion it follows, so reconciliation sees it was taken
    public var memoId: Int?

    public init(book: String, ticker: String, asset: String = "stock", side: String, quantity: String, fill: String,
                at: String, fees: String = "0", broker: String? = nil, note: String? = nil, optionSymbol: String? = nil,
                lane: String? = nil, ideaId: Int? = nil, memoId: Int? = nil) {
        self.book = book
        self.ticker = ticker
        self.asset = asset
        self.side = side
        self.quantity = quantity
        self.fill = fill
        self.at = at
        self.fees = fees
        self.broker = broker
        self.note = note
        self.optionSymbol = optionSymbol
        self.lane = lane
        self.ideaId = ideaId
        self.memoId = memoId
    }
}

public struct ClosedTrade: Codable, Sendable, Equatable, Identifiable {
    public var id: Int { tradeId }
    public let tradeId: Int
    public let book: String?
    public let ticker: String
    public let asset: String
    public let lane: String?
    public let quantity: String
    public let at: String
    public let proceeds: String
    public let cost: String
    public let pnl: String
    public let pnlPct: String?
    public let openedTradeIds: [Int]
    public let ideaId: Int?
    public let memoId: Int?
    public let attachments: Int?
}

public struct OpenPosition: Codable, Sendable, Equatable, Identifiable {
    public var id: String { "\(book ?? "")/\(ticker)" }
    public let book: String?
    public let ticker: String
    public let quantity: String
    public let averageCost: String
    public let costBasis: String
    public let price: String?
    public let priceSource: String?
    public let priceAsOf: String?
    public let unrealizedPnl: String?
}

public struct HistoryTotals: Codable, Sendable, Equatable {
    public let trades: Int
    public let winRate: String?
    public let averageWin: String?
    public let averageLoss: String?
    public let netPnl: String
}

public struct History: Codable, Sendable, Equatable {
    public let closed: [ClosedTrade]
    public let open: [OpenPosition]
    public let totals: HistoryTotals
}

public struct PortfolioPosition: Codable, Sendable, Equatable, Identifiable {
    public var id: String { ticker }
    public let ticker: String
    public let quantity: String
    public let averageCost: String
    public let costBasis: String
    public let price: String?
    public let priceSource: String?
    public let priceAsOf: String?
    public let value: String?
    public let unrealizedPnl: String?
    public let dayPnl: String?          // nil: no quote, or no close before its session
    public let lane: String?
}

public struct Allocation: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let share: String?
}

public struct PortfolioBook: Codable, Sendable, Equatable, Identifiable {
    public var id: String { book }
    public let book: String
    public let name: String
    public let cash: String
    public let invested: String
    public let total: String
    public let priced: Bool
    public let netDeposits: String
    public let totalPnl: String?
    public let dayPnl: String?
    public let dayPnlPartial: Bool?     // some position's day P&L is unknown, so the book's leaves it out
    public let positions: [PortfolioPosition]
    public let allocation: [Allocation]
}

/// The idea a position was bought on: its one-sentence reason, stop and target.
public struct PositionThesis: Codable, Sendable, Equatable {
    public let ideaId: Int
    public let lane: String
    public let module: String
    public let status: String
    public let entry: String?
    public let stop: String?
    public let target: String?
    public let reason: String
}

/// One stock in one book, as the position screen shows it.
public struct PositionDetail: Codable, Sendable, Equatable {
    public let book: String
    public let ticker: String
    public let quantity: String
    public let averageCost: String?
    public let costBasis: String?
    public let price: String?
    public let priceSource: String?
    public let priceAsOf: String?
    public let value: String?
    public let dayPnl: String?
    public let unrealizedPnl: String?
    public let realizedPnl: String
    public let totalPnl: String?
    public let thesis: PositionThesis?
    public let memo: Memo?
    public let news: [NewsRow]
    public let nextEarnings: EarningsRow?
}

public struct Portfolio: Codable, Sendable, Equatable {
    public let books: [PortfolioBook]
    public let total: String
    public let note: String?
}

public struct RiskRuleRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { key }
    public let key: String
    public let kind: String          // share | count | switch
    public let suggested: String
    public let label: String
    public let value: String?
    public let enabled: Bool?
    public let updatedAt: String?
}

public struct RiskRules: Codable, Sendable, Equatable {
    public let rules: [RiskRuleRow]
    public let missing: [String]
    public let complete: Bool
}

public struct RuleInput: Codable, Sendable, Equatable {
    public let key: String
    public let value: String
    public let enabled: Bool

    public init(key: String, value: String, enabled: Bool) {
        self.key = key
        self.value = value
        self.enabled = enabled
    }
}

public struct AttachmentRow: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let tradeId: Int?
    public let contentType: String
    public let bytes: Int
    public let filename: String?
    public let createdAt: String
}

public struct TradeDetail: Codable, Sendable, Equatable {
    public let id: Int
    public let book: String
    public let ticker: String
    public let asset: String
    public let side: String
    public let quantity: String
    public let fill: String
    public let at: String
    public let fees: String
    public let broker: String?
    public let note: String?
    public let lane: String?
    public let attachmentList: [AttachmentRow]
}

public struct TradeFilters: Sendable, Equatable {
    public var book: String?
    public var ticker: String?
    public var since: String?
    public var until: String?
    public var side: String?
    public var asset: String?
    public var lane: String?
    public var result: String?

    public init(book: String? = nil, ticker: String? = nil, since: String? = nil, until: String? = nil,
                side: String? = nil, asset: String? = nil, lane: String? = nil, result: String? = nil) {
        self.book = book
        self.ticker = ticker
        self.since = since
        self.until = until
        self.side = side
        self.asset = asset
        self.lane = lane
        self.result = result
    }

    public var queryItems: [URLQueryItem] {
        [("book", book), ("ticker", ticker), ("since", since), ("until", until), ("side", side), ("asset", asset),
         ("lane", lane), ("result", result)].compactMap { name, value in
            value.flatMap { $0.isEmpty ? nil : URLQueryItem(name: name, value: $0) }
        }
    }
}

/// ISO 8601 for the backend, with fractional seconds tolerated on the way back.
public enum Stamp {
    public static func string(_ date: Date) -> String {
        date.formatted(.iso8601)
    }

    /// The calendar day a person sees for `date`, as "yyyy-MM-dd" (a day picked in a form, "today"). `.iso8601` writes
    /// the UTC day, which in Pacific time is already tomorrow from 17:00 (16:00 in summer).
    public static func localDay(_ date: Date, in zone: TimeZone = .current) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let c = cal.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    public static func date(_ iso: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: iso) ?? ISO8601DateFormatter().date(from: iso)
    }

    /// A day as "Oct 28, 2026". A date with no time (an earnings day, a series point) is a calendar day, not a
    /// moment, so it is read and shown in UTC: shown in the device's zone it would move a day west of Greenwich.
    public static func day(_ iso: String, in zone: TimeZone = .current) -> String {
        var style = Date.FormatStyle(date: .abbreviated, time: .omitted)
        if let d = date(iso) {
            style.timeZone = zone                                   // a moment: the day it is where it is read
            return d.formatted(style)
        }
        guard let d = try? Date(iso, strategy: .iso8601.year().month().day()) else { return iso }
        style.timeZone = TimeZone(identifier: "UTC") ?? zone        // a calendar day: the day as written
        return d.formatted(style)
    }

    /// A trading day as a chart's x value: local midnight of the day the stamp names, so a chart in any zone puts the
    /// mark under its own day's label. A daily bar is stamped at New York midnight (04:00 or 05:00 UTC, both data
    /// providers), which in Pacific time is the evening before; a date with no time is the day as written.
    public static func tradingDay(_ iso: String, in calendar: Calendar = .current) -> Date? {
        var source = Calendar(identifier: .gregorian)
        let parts: DateComponents
        if let instant = date(iso) {
            source.timeZone = TimeZone(identifier: "America/New_York") ?? .current
            parts = source.dateComponents([.year, .month, .day], from: instant)
        } else if let day = try? Date(iso, strategy: .iso8601.year().month().day()) {
            source.timeZone = TimeZone(identifier: "UTC") ?? .current
            parts = source.dateComponents([.year, .month, .day], from: day)
        } else {
            return nil
        }
        return calendar.date(from: parts)
    }

    /// "Oct 30, 2026 at 2:30 PM" style, or the raw string when it cannot be read.
    public static func short(_ iso: String?) -> String {
        guard let iso, let d = date(iso) else { return iso ?? "—" }
        return d.formatted(date: .abbreviated, time: .shortened)
    }
}
