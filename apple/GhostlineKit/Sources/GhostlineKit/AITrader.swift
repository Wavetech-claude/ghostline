import Foundation

// The AI trader against its S&P 500 ghost, options practice on any amount, and value over time.

public struct TraderRun: Codable, Sendable, Equatable {
    public let kind: String
    public let at: String
    public let result: JSONValue
}

public struct TraderState: Codable, Sendable, Equatable {
    public let book: String
    public let cash: String
    public let available: String
    public let unsettled: String
    public let lockedCollateral: String
    public let equity: String
    public let openPositions: Int
    public let shortPuts: Int
    public let lastRuns: [TraderRun]
}

public struct TraderTrade: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ticker: String
    public let asset: String
    public let side: String
    public let quantity: String
    public let fill: String
    public let fees: String
    public let at: String
    public let note: String?
    public let optionSymbol: String?
    public let lane: String?
    public let ideaId: Int?
    public let module: String?
    public let copyRealAllowed: Bool
    public let copyRealReason: String?
}

public struct SeriesPoint: Codable, Sendable, Equatable, Identifiable {
    public var id: String { date }
    public let date: String
    public let value: String
    public let netFlow: String
    public let twrPct: String

    public var day: Date? { Stamp.date(date + "T00:00:00Z") }
}

public struct TraderSeries: Codable, Sendable, Equatable {
    public let ai: [SeriesPoint]
    public let ghost: [SeriesPoint]
}

public struct TwrPair: Codable, Sendable, Equatable {
    public let ai: String?
    public let ghost: String?
}

public struct LaneState: Codable, Sendable, Equatable, Identifiable {
    public var id: String { lane }
    public let lane: String
    public let settledN: Int
    public let graduated: Bool
}

public struct AITraderAnswer: Codable, Sendable, Equatable {
    public let state: TraderState
    public let trades: [TraderTrade]
    public let series: TraderSeries
    public let twrPct: TwrPair
    public let ghostTrades: [TraderTrade]
    public let lanes: [LaneState]
}

public struct OptionsPracticeAnswer: Codable, Sendable, Equatable {
    public let state: TraderState
    public let trades: [TraderTrade]
    public let series: [SeriesPoint]
    public let twrPct: String?
}

public extension APIClient {
    func aiTrader() async throws -> AITraderAnswer {
        try await get("/ai-trader", authenticated: true)
    }

    func runAITrader() async throws -> JSONValue {
        struct Empty: Encodable {}
        return try await post("/ai-trader/run", body: Empty(), authenticated: true)
    }

    /// Repeats an AI trade in practice, or (for a graduated lane) puts it on the to-do list for real money.
    func copyTrade(id: Int, to: String) async throws -> JSONValue {
        struct Body: Encodable { let to: String }
        return try await post("/ai-trader/trades/\(id)/copy", body: Body(to: to), authenticated: true)
    }

    func optionsPractice() async throws -> OptionsPracticeAnswer {
        try await get("/options-practice", authenticated: true)
    }

    func runOptionsPractice() async throws -> JSONValue {
        struct Empty: Encodable {}
        return try await post("/options-practice/run", body: Empty(), authenticated: true)
    }
}
