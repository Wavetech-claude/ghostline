import Foundation

// Price alerts and monthly statements.

public struct PriceAlert: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ticker: String
    public let kind: String            // above | below | pct_move | ma_cross | earnings
    public let value: String
    public let status: String          // active | triggered | off
    public let note: String?
    public let message: String?
    public let createdAt: String
    public let lastChecked: String?
    public let triggeredAt: String?

    /// The rule in words: "Above 200", "Moves 5% in a day", "Crosses its 50-day average", "Earnings within 3 days".
    public var rule: String {
        switch kind {
        case "above": "Above \(value)"
        case "below": "Below \(value)"
        case "pct_move": "Moves \(value)% in a day"
        case "ma_cross": "Crosses its \(Int(Double(value) ?? 0))-day average"
        case "earnings": "Earnings within \(Int(Double(value) ?? 0)) days"
        default: kind
        }
    }
}

public struct StatementFlow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { at + amount }
    public let at: String
    public let amount: String
    public let note: String?
}

public struct StatementTrade: Codable, Sendable, Equatable, Identifiable {
    public var id: String { at + ticker + side + quantity }
    public let at: String
    public let ticker: String
    public let asset: String
    public let side: String
    public let quantity: String
    public let fill: String
    public let fees: String
    public let optionSymbol: String?
    public let note: String?
}

public struct Statement: Codable, Sendable, Equatable {
    public let book: String
    public let name: String
    public let month: String
    public let deposits: String
    public let withdrawals: String
    public let fees: String
    public let realizedPnl: String
    public let dividends: String?
    public let dividendsNote: String
    public let cashAtEnd: String
    public let flows: [StatementFlow]
    public let trades: [StatementTrade]
}

public extension APIClient {
    func alerts(ticker: String? = nil) async throws -> [PriceAlert] {
        struct Answer: Decodable { let alerts: [PriceAlert] }
        let q = ticker.map { [URLQueryItem(name: "ticker", value: $0)] } ?? []
        return try await (get("/alerts", query: q, authenticated: true) as Answer).alerts
    }

    func addAlert(ticker: String, kind: String, value: String, note: String? = nil) async throws -> PriceAlert {
        struct Body: Encodable { let ticker: String; let kind: String; let value: String; let note: String? }
        return try await post("/alerts", body: Body(ticker: ticker, kind: kind, value: value, note: note), authenticated: true)
    }

    func setAlert(id: Int, active: Bool) async throws -> PriceAlert {
        struct Body: Encodable { let active: Bool }
        return try await put("/alerts/\(id)", body: Body(active: active), authenticated: true)
    }

    func deleteAlert(id: Int) async throws {
        struct Ack: Decodable { let ok: Bool }
        let r = try request("/alerts/\(id)", method: "DELETE", authenticated: true)
        _ = try await send(r) as Ack
    }

    func statement(book: String, month: String) async throws -> Statement {
        try await get("/statements", query: [URLQueryItem(name: "book", value: book), URLQueryItem(name: "month", value: month)],
                      authenticated: true)
    }

    /// The statement as a file to share: "csv" or "pdf".
    func statementFile(book: String, month: String, format: String) async throws -> Data {
        let r = try request("/statements", method: "GET", authenticated: true,
                            query: [URLQueryItem(name: "book", value: book), URLQueryItem(name: "month", value: month),
                                    URLQueryItem(name: "format", value: format)])
        return try await sendRaw(r).0
    }
}
