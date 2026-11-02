import Foundation

// Edit, void and reset (backend 0.24.0). Nothing in the ledger is deleted: a real-book trade or deposit is edited
// (every change kept) or voided (kept, struck through, left out of every number) and can be unvoided; a practice
// order is replaced; a practice, AI or options book is reset, which voids its entries and cancels its open orders.

/// One change to an entry: an edit (one field, old and new), a void (with its reason) or an unvoid.
public struct LedgerEdit: Codable, Sendable, Equatable, Identifiable {
    public var id: String { "\(at)-\(action)-\(field ?? "")" }
    public let action: String          // edit | void | unvoid
    public let field: String?
    public let old: String?
    public let new: String?
    public let reason: String?
    public let byWhom: String
    public let at: String
}

/// The fields of a real-book trade to change. Only the fields set are sent; `clear` sends the listed text fields
/// (broker, note, option symbol, lane) as empty.
public struct TradeChanges: Encodable, Sendable, Equatable {
    public var ticker: String?
    public var asset: String?
    public var side: String?
    public var quantity: String?
    public var fill: String?
    public var at: String?
    public var fees: String?
    public var broker: String?
    public var note: String?
    public var optionSymbol: String?
    public var lane: String?
    public var clear: Set<String> = []

    public init() {}

    public var isEmpty: Bool {
        [ticker, asset, side, quantity, fill, at, fees, broker, note, optionSymbol, lane].allSatisfy { $0 == nil }
            && clear.isEmpty
    }

    private enum CodingKeys: String, CodingKey {
        case ticker, asset, side, quantity, fill, at, fees, broker, note, optionSymbol, lane
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        let values: [(CodingKeys, String?)] = [(.ticker, ticker), (.asset, asset), (.side, side), (.quantity, quantity),
                                               (.fill, fill), (.at, at), (.fees, fees), (.broker, broker),
                                               (.note, note), (.optionSymbol, optionSymbol), (.lane, lane)]
        for (key, value) in values {
            if let value {
                try c.encode(value, forKey: key)
            } else if clear.contains(key.stringValue) {
                try c.encodeNil(forKey: key)
            }
        }
    }
}

/// The fields of a real-book deposit or withdrawal to change; only the fields set are sent.
public struct CashFlowChanges: Encodable, Sendable, Equatable {
    public var amount: String?
    public var at: String?
    public var note: String?

    public init(amount: String? = nil, at: String? = nil, note: String? = nil) {
        self.amount = amount
        self.at = at
        self.note = note
    }

    private enum CodingKeys: String, CodingKey { case amount, at, note }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(amount, forKey: .amount)
        try c.encodeIfPresent(at, forKey: .at)
        try c.encodeIfPresent(note, forKey: .note)
    }
}

/// A deposit or withdrawal with its edit history.
public struct CashFlowDetail: Codable, Sendable, Equatable {
    public let id: Int
    public let book: String
    public let amount: String
    public let at: String
    public let note: String?
    public let voidedAt: String?
    public let voidReason: String?
    public let edits: [LedgerEdit]
}

/// What a replacement order may change; the type, side, ticker and book stay.
public struct ReplaceInput: Encodable, Sendable, Equatable {
    public var quantity: String?
    public var limitPrice: String?
    public var stopPrice: String?
    public var tif: String?

    public init(quantity: String? = nil, limitPrice: String? = nil, stopPrice: String? = nil, tif: String? = nil) {
        self.quantity = quantity
        self.limitPrice = limitPrice
        self.stopPrice = stopPrice
        self.tif = tif
    }

    private enum CodingKeys: String, CodingKey { case quantity, limitPrice, stopPrice, tif }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(quantity, forKey: .quantity)
        try c.encodeIfPresent(limitPrice, forKey: .limitPrice)
        try c.encodeIfPresent(stopPrice, forKey: .stopPrice)
        try c.encodeIfPresent(tif, forKey: .tif)
    }
}

/// What a reset did.
public struct ResetResult: Codable, Sendable, Equatable {
    public let book: String
    public let reason: String
    public let trades: Int
    public let cashFlows: Int
    public let ordersCancelled: Int
    public let depositId: Int?
}

public extension OrderRow {
    /// Robinhood's rule: an open limit, stop or stop-limit order can be replaced; a market order is cancelled.
    var isReplaceable: Bool { isOpen && ["limit", "stop", "stop_limit"].contains(type) }
}

public extension APIClient {
    private struct Changed: Decodable { let id: Int }
    private struct Reason: Encodable { let reason: String }
    private struct Nothing: Encodable {}
    private struct ResetBody: Encodable {
        let book: String
        let deposit: String?
    }

    func editTrade(id: Int, _ changes: TradeChanges) async throws {
        let _: Changed = try await patch("/trades/\(id)", body: changes, authenticated: true)
    }

    func voidTrade(id: Int, reason: String) async throws {
        let _: Changed = try await post("/trades/\(id)/void", body: Reason(reason: reason), authenticated: true)
    }

    func unvoidTrade(id: Int) async throws {
        let _: Changed = try await post("/trades/\(id)/unvoid", body: Nothing(), authenticated: true)
    }

    func cashFlow(id: Int) async throws -> CashFlowDetail {
        try await get("/cash-flows/\(id)", authenticated: true)
    }

    func editCashFlow(id: Int, _ changes: CashFlowChanges) async throws {
        let _: Changed = try await patch("/cash-flows/\(id)", body: changes, authenticated: true)
    }

    func voidCashFlow(id: Int, reason: String) async throws {
        let _: Changed = try await post("/cash-flows/\(id)/void", body: Reason(reason: reason), authenticated: true)
    }

    func unvoidCashFlow(id: Int) async throws {
        let _: Changed = try await post("/cash-flows/\(id)/unvoid", body: Nothing(), authenticated: true)
    }

    func replaceOrder(id: Int, _ input: ReplaceInput) async throws -> OrderRow {
        try await post("/practice/orders/\(id)/replace", body: input, authenticated: true)
    }

    func resetBook(_ book: String, deposit: String? = nil) async throws -> ResetResult {
        try await post("/practice/reset", body: ResetBody(book: book, deposit: deposit), authenticated: true)
    }
}
