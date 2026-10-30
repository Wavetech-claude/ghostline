import Foundation

// The practice account as the backend serves it: order tickets and their fills, the account's buying power,
// option expirations and chains with the collateral each put needs.

public struct OrderRow: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let book: String?
    public let ticker: String
    public let asset: String
    public let side: String
    public let type: String
    public let tif: String
    public let quantity: String
    public let limitPrice: String?
    public let stopPrice: String?
    public let stopLoss: String?
    public let optionSymbol: String?
    public let status: String          // open | filled | cancelled | expired | refused
    public let reason: String?
    public let triggered: Bool
    public let placedAt: String
    public let filledAt: String?
    public let fillPrice: String?
    public let tradeId: Int?
    public let note: String?

    public var isOpen: Bool { status == "open" }
    public var isRefused: Bool { status == "refused" }
}

public struct OrderInput: Codable, Sendable, Equatable {
    public var book: String
    public var ticker: String
    public var asset: String
    public var side: String
    public var type: String
    public var tif: String
    public var quantity: String
    public var limitPrice: String?
    public var stopPrice: String?
    public var stopLoss: String?
    public var optionSymbol: String?
    public var note: String?

    public init(book: String = "practice", ticker: String, asset: String = "stock", side: String, type: String = "market",
                tif: String = "day", quantity: String, limitPrice: String? = nil, stopPrice: String? = nil,
                stopLoss: String? = nil, optionSymbol: String? = nil, note: String? = nil) {
        self.book = book
        self.ticker = ticker
        self.asset = asset
        self.side = side
        self.type = type
        self.tif = tif
        self.quantity = quantity
        self.limitPrice = limitPrice
        self.stopPrice = stopPrice
        self.stopLoss = stopLoss
        self.optionSymbol = optionSymbol
        self.note = note
    }
}

public struct ShortPut: Codable, Sendable, Equatable, Identifiable {
    public var id: String { optionSymbol }
    public let optionSymbol: String
    public let underlying: String?
    public let expiry: String?
    public let strike: String?
    public let contracts: String
    public let collateral: String?
}

public struct PendingSettlement: Codable, Sendable, Equatable, Identifiable {
    public var id: Int { tradeId }
    public let tradeId: Int
    public let amount: String
    public let settlesAt: String
}

public struct PracticeAccount: Codable, Sendable, Equatable {
    public let book: String
    public let cash: String
    public let unsettled: String
    public let lockedCollateral: String
    public let available: String
    public let equity: String
    public let openPositions: Int
    public let paperDrawdownShare: String
    public let feePerOrder: String
    public let shortPuts: [ShortPut]
    public let openOrders: [OrderRow]
    public let pendingSettlements: [PendingSettlement]
}

public struct Expirations: Codable, Sendable, Equatable {
    public let ticker: String
    public let expirations: [String]
    public let configured: Bool
    public let reason: String?
}

public struct OptionRow: Codable, Sendable, Equatable, Identifiable {
    public var id: String { optionSymbol }
    public let optionSymbol: String
    public let underlying: String
    public let expiry: String
    public let strike: String
    public let kind: String            // put | call
    public let bid: String?
    public let ask: String?
    public let last: String?
    public let volume: String?
    public let openInterest: String?
    public let iv: String?
    public let delta: String?
    public let collateral: String?
    public let covered: Bool
}

public struct OptionChain: Codable, Sendable, Equatable {
    public let ticker: String
    public let expiry: String
    public let configured: Bool
    public let reason: String?
    public let source: String
    public let asOf: String?
    public let available: String
    public let book: String
    public let options: [OptionRow]

    public var puts: [OptionRow] { options.filter { $0.kind == "put" } }
    public var calls: [OptionRow] { options.filter { $0.kind == "call" } }
}

public extension APIClient {
    func placeOrder(_ input: OrderInput) async throws -> OrderRow {
        try await post("/practice/orders", body: input, authenticated: true)
    }

    func orders(book: String? = nil, status: String? = nil) async throws -> [OrderRow] {
        struct Answer: Decodable { let orders: [OrderRow] }
        var q: [URLQueryItem] = []
        if let book { q.append(URLQueryItem(name: "book", value: book)) }
        if let status { q.append(URLQueryItem(name: "status", value: status)) }
        return try await (get("/practice/orders", query: q, authenticated: true) as Answer).orders
    }

    func cancelOrder(id: Int) async throws -> OrderRow {
        var r = try request("/practice/orders/\(id)", method: "DELETE", authenticated: true)
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await send(r)
    }

    func practiceAccount(book: String = "practice") async throws -> PracticeAccount {
        try await get("/practice/account", query: [URLQueryItem(name: "book", value: book)], authenticated: true)
    }

    func setPracticeFee(_ fee: String) async throws {
        struct Body: Encodable { let feePerOrder: String }
        struct Ack: Decodable { let feePerOrder: String }
        _ = try await put("/practice/settings", body: Body(feePerOrder: fee), authenticated: true) as Ack
    }

    func expirations(ticker: String) async throws -> Expirations {
        try await get("/options/expirations", query: [URLQueryItem(name: "ticker", value: ticker)], authenticated: true)
    }

    func chain(ticker: String, expiry: String, book: String = "practice") async throws -> OptionChain {
        try await get("/options/chain", query: [URLQueryItem(name: "ticker", value: ticker),
                                                URLQueryItem(name: "expiry", value: expiry),
                                                URLQueryItem(name: "book", value: book)], authenticated: true)
    }
}
