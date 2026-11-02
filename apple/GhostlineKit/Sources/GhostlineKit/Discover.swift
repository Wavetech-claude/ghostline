import Foundation

// Discover: what moved, where, and in which theme. Today's unfiltered lists are Alpaca's live ones; a week, a month,
// only ETFs or one industry come from the whole market's daily file.

/// One stock or fund on a movers list.
public struct MoverRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { ticker }
    public let ticker: String
    public let name: String?
    public let kind: String?
    public let industry: String?
    public let price: String?
    public let changePct: String?
    public let dollarVolume: String?      // over the period, from the daily file
    public let volume: String?            // shares today, from Alpaca's most-active list
}

public struct MoversList: Codable, Sendable, Equatable {
    public let period: String             // day, week, month
    public let direction: String          // gainers, losers, active
    public let source: String             // alpaca (live) or massive (the daily file)
    public let through: String?           // the list's time (alpaca) or last session (massive)
    public let since: String?             // the session the move is measured from (massive)
    public let rows: [MoverRow]

    public var isLive: Bool { source == "alpaca" }
}

public struct SectorRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { etf }
    public let name: String
    public let etf: String
    public let changePct: String?
}

public struct SectorList: Codable, Sendable, Equatable {
    public let period: String
    public let through: String?
    public let rows: [SectorRow]
}

public struct IndustryRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let stocks: Int
    public let changePct: String           // the median move of its stocks
    public let best: String?
}

public struct IndustryList: Codable, Sendable, Equatable {
    public let period: String
    public let through: String?
    public let tagged: Int
    public let rows: [IndustryRow]
}

public struct IndustryName: Codable, Sendable, Equatable, Identifiable, Hashable {
    public var id: String { name }
    public let name: String
    public let stocks: Int
}

public struct ThemeFund: Codable, Sendable, Equatable, Identifiable {
    public var id: String { ticker }
    public let ticker: String
    public let name: String?
    public let changePct: String?
}

public struct MarketTheme: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let note: String?
    public let etfs: [ThemeFund]
    public let industries: [String]
    public let leaders: [MoverRow]
}

public struct ThemeList: Codable, Sendable, Equatable {
    public let period: String
    public let through: String?
    public let themes: [MarketTheme]
}

public extension APIClient {
    /// GET /discover/movers. `minPrice` 0 shows stocks under $5 too.
    func movers(period: String = "day", direction: String = "gainers", kind: String = "all", industry: String? = nil,
                minPrice: Int = 5, limit: Int = 25) async throws -> MoversList {
        var items = [URLQueryItem(name: "period", value: period), URLQueryItem(name: "direction", value: direction),
                     URLQueryItem(name: "kind", value: kind), URLQueryItem(name: "min_price", value: String(minPrice)),
                     URLQueryItem(name: "limit", value: String(limit))]
        if let industry { items.append(URLQueryItem(name: "industry", value: industry)) }
        return try await get("/discover/movers", query: items, authenticated: true)
    }

    func sectors(period: String = "day") async throws -> SectorList {
        try await get("/discover/sectors", query: [URLQueryItem(name: "period", value: period)], authenticated: true)
    }

    func industries(period: String = "day") async throws -> IndustryList {
        try await get("/discover/industries", query: [URLQueryItem(name: "period", value: period)], authenticated: true)
    }

    func industryNames() async throws -> [IndustryName] {
        struct Answer: Decodable { let industries: [IndustryName] }
        return try await (get("/discover/industry-names", authenticated: true) as Answer).industries
    }

    func themes(period: String = "week") async throws -> ThemeList {
        try await get("/discover/themes", query: [URLQueryItem(name: "period", value: period)], authenticated: true)
    }
}
