import Foundation

// Reconciliation: what the app suggested against what the owner did (took, declined, lapsed, open; own calls), each
// with its paper result and, when taken, its realized P&L; and the real book against a broker statement.

public struct Reconciliation: Codable, Sendable, Equatable {
    public let month: String
    public let suggestions: [SuggestionRow]
    public let ownCalls: [OwnCall]
    public let summary: ReconcileSummary
    public let broker: BrokerCheck?
}

public struct SuggestionRow: Codable, Sendable, Equatable {
    public let kind: String             // idea | memo
    public let id: Int                  // the idea's or the memo's id
    public let ticker: String
    public let label: String            // the idea's lane, or "buy call"
    public let at: String
    public let outcome: String          // took | declined | lapsed | open
    public let reason: String?
    public let suggested: Suggested
    public let taken: Taken?
    public let paper: PaperResult?
    public let realized: String?

    /// Ideas and memos number separately, so a row's identity is its kind and id together.
    public var key: String { "\(kind)-\(id)" }

    public struct Suggested: Codable, Sendable, Equatable {
        public let entry: String?
        public let quantity: String?
    }

    public struct Taken: Codable, Sendable, Equatable {
        public let tradeId: Int
        public let quantity: String
        public let fill: String
        public let at: String
    }

    public struct PaperResult: Codable, Sendable, Equatable {
        public let status: String?
        public let resultPct: String?
        public let excessPct: String?
    }
}

public struct OwnCall: Codable, Sendable, Equatable, Identifiable {
    public let tradeId: Int
    public let ticker: String
    public let side: String
    public let quantity: String
    public let fill: String
    public let at: String
    public let realized: String?
    public var id: Int { tradeId }
}

public struct OutcomeSummary: Codable, Sendable, Equatable {
    public let n: Int
    public let paperSettled: Int?
    public let paperExcessAvg: String?
    public let realized: String?
}

public struct ReconcileSummary: Codable, Sendable, Equatable {
    public let took: OutcomeSummary
    public let declined: OutcomeSummary
    public let lapsed: OutcomeSummary
    public let open: OutcomeSummary
    public let ownCalls: OutcomeSummary
}

public struct BrokerCheck: Codable, Sendable, Equatable {
    public let statementId: Int
    public let asOf: String
    public let shares: [ShareLine]
    public let cash: CashLine
    public let matches: Bool
    public let note: String

    public struct ShareLine: Codable, Sendable, Equatable, Identifiable {
        public let ticker: String
        public let app: String
        public let broker: String
        public let difference: String
        public let match: Bool
        public var id: String { ticker }
    }

    public struct CashLine: Codable, Sendable, Equatable {
        public let app: String
        public let broker: String
        public let difference: String
        public let match: Bool
    }
}

public struct StatementInput: Codable, Sendable, Equatable {
    public var asOf: String
    public var cash: String
    public var positions: [Holding]
    public var note: String?

    public struct Holding: Codable, Sendable, Equatable {
        public var ticker: String
        public var quantity: String
        public init(ticker: String, quantity: String) {
            self.ticker = ticker
            self.quantity = quantity
        }
    }

    public init(asOf: String, cash: String, positions: [Holding], note: String? = nil) {
        self.asOf = asOf
        self.cash = cash
        self.positions = positions
        self.note = note
    }
}

public extension APIClient {
    func reconciliation(month: String? = nil) async throws -> Reconciliation {
        try await get("/reconciliation", query: month.map { [URLQueryItem(name: "month", value: $0)] } ?? [],
                      authenticated: true)
    }

    /// The owner says no to an idea or a memo's buy call, with an optional reason.
    func decline(kind: String, refId: Int, reason: String?) async throws {
        struct Body: Encodable { let kind: String; let refId: Int; let reason: String? }
        struct Ack: Decodable { let outcome: String }
        _ = try await post("/decisions", body: Body(kind: kind, refId: refId, reason: reason), authenticated: true) as Ack
    }

    func undoDecline(kind: String, refId: Int) async throws {
        struct Ack: Decodable { let undone: Bool }
        var r = try request("/decisions/\(kind)/\(refId)", method: "DELETE", authenticated: true)
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        _ = try await send(r) as Ack
    }

    func addStatement(_ statement: StatementInput) async throws -> BrokerCheck {
        try await post("/broker-statements", body: statement, authenticated: true)
    }
}
