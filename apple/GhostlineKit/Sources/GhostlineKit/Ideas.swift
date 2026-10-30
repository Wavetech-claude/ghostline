import Foundation

// Ideas by lane and the signals behind them, as the backend serves them.

public struct IdeaRow: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let lane: String                // long | swing | puts
    public let ticker: String
    public let signalId: Int
    public let module: String
    public let status: String              // proposed | open | refused | expired | settled
    public let book: String
    public let balance: String
    public let entry: String?
    public let stop: String?
    public let target: String?
    public let quantity: String?
    public let sizeShare: String?
    public let maxLoss: String?
    public let collateral: String?
    public let optionSymbol: String?
    public let refusal: String?
    public let opensAt: String
    public let expiresAt: String
    public let score: String
    public let reason: String
    public let horizonDays: Int
    public let asOf: String
    public let source: String?
    public let direction: String

    public var isOpen: Bool { status == "open" }
}

public struct IdeaCount: Codable, Sendable, Equatable {
    public let lane: String
    public let status: String
    public let n: Int
}

public struct LastScan: Codable, Sendable, Equatable {
    public let day: String?
    public let signals: Int
}

public struct Ideas: Codable, Sendable, Equatable {
    public let ideas: [IdeaRow]
    public let counts: [IdeaCount]
    public let lastScan: LastScan
}

public struct SignalRow: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ticker: String
    public let module: String
    public let lane: String
    public let direction: String
    public let score: String
    public let actionable: Bool
    public let horizonDays: Int
    public let entry: String?
    public let stop: String?
    public let target: String?
    public let reason: String
    public let evidence: JSONValue
    public let day: String
    public let source: String?
    public let asOf: String
}

public struct IdeaDetail: Codable, Sendable, Equatable {
    public let id: Int
    public let lane: String
    public let ticker: String
    public let module: String
    public let status: String
    public let refusal: String?
    public let reason: String
    public let score: String
    public let entry: String?
    public let stop: String?
    public let target: String?
    public let quantity: String?
    public let collateral: String?
    public let signal: SignalRow?
}

public struct ScanResult: Codable, Sendable, Equatable {
    public let day: String
    public let tickers: Int
    public let signals: Int
    public let ideas: Int
    public let refused: Int
    public let skipped: [String]
    public let expired: Int?
}

/// Untyped JSON for a module's evidence: shown as key–value lines, never interpreted.
public enum JSONValue: Codable, Sendable, Equatable {
    case string(String), number(Double), bool(Bool), null
    case array([JSONValue]), object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let n): try c.encode(n)
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    /// One line per top-level key, scalars only; nested values are summarised by their kind.
    public var lines: [(String, String)] {
        guard case .object(let o) = self else { return [] }
        return o.keys.sorted().map { key in
            switch o[key]! {
            case .string(let s): return (key, s)
            case .number(let n): return (key, n == n.rounded() && abs(n) < 1e9 ? String(Int(n)) : String(format: "%.4g", n))
            case .bool(let b): return (key, b ? "yes" : "no")
            case .null: return (key, "—")
            case .array(let a): return (key, "\(a.count) items")
            case .object(let d): return (key, "\(d.count) fields")
            }
        }
    }
}

public extension APIClient {
    func ideas(lane: String? = nil) async throws -> Ideas {
        let q = lane.map { [URLQueryItem(name: "lane", value: $0)] } ?? []
        return try await get("/ideas", query: q, authenticated: true)
    }

    func idea(id: Int) async throws -> IdeaDetail {
        try await get("/ideas/\(id)", authenticated: true)
    }

    func signals(ticker: String) async throws -> [SignalRow] {
        struct Answer: Decodable { let signals: [SignalRow] }
        return try await (get("/signals", query: [URLQueryItem(name: "ticker", value: ticker)], authenticated: true) as Answer).signals
    }

    func marketRegime() async throws -> SignalRow? {
        struct Answer: Decodable { let regime: SignalRow? }
        return try await (get("/market/regime", authenticated: true) as Answer).regime
    }

    func scanNow() async throws -> ScanResult {
        struct Empty: Encodable {}
        return try await post("/ideas/scan", body: Empty(), authenticated: true)
    }
}
