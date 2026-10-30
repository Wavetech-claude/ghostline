import Foundation

// "Invest this": an amount split across the current ideas, every split through the owner's rules; approved into
// the practice account or onto the to-do list for real money. The to-do list itself.

public struct InvestSplit: Codable, Sendable, Equatable, Identifiable {
    public var id: Int { ideaId }
    public let ideaId: Int
    public let ticker: String
    public let lane: String
    public let module: String
    public let score: String
    public let reason: String
    public let dollars: String
    public let price: String?
    public let priceSource: String?
    public let shares: String?
    public let stop: String?
    public let maxLoss: String?
    public let decision: String        // pass | refuse
    public let why: String

    public var passed: Bool { decision == "pass" }
}

public struct InvestPlan: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let amount: String
    public let forDate: String
    public let book: String
    public let splits: [InvestSplit]
    public let unallocated: String
    public let status: String          // proposed | approved | placed | dropped
    public let approvedTo: String?
    public let createdAt: String
    public let note: String?
}

public struct TodoItem: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let source: String          // invest | ai_copy
    public let ticker: String
    public let asset: String
    public let side: String
    public let quantity: String
    public let limitPrice: String?
    public let optionSymbol: String?
    public let lane: String?
    public let ideaId: Int?
    public let note: String?
    public let status: String
    public let createdAt: String
}

public extension APIClient {
    func invest(amount: String, forDate: String? = nil, book: String = "practice") async throws -> InvestPlan {
        struct Body: Encodable { let amount: String; let forDate: String?; let book: String }
        return try await post("/invest", body: Body(amount: amount, forDate: forDate, book: book), authenticated: true)
    }

    func approvePlan(id: Int, to: String, deposit: Bool = true) async throws -> InvestPlan {
        struct Body: Encodable { let to: String; let deposit: Bool }
        return try await post("/invest/\(id)/approve", body: Body(to: to, deposit: deposit), authenticated: true)
    }

    func dropPlan(id: Int) async throws {
        struct Empty: Encodable {}
        struct Ack: Decodable { let ok: Bool }
        _ = try await post("/invest/\(id)/drop", body: Empty(), authenticated: true) as Ack
    }

    func todos() async throws -> [TodoItem] {
        struct Answer: Decodable { let todos: [TodoItem] }
        return try await (get("/todos", authenticated: true) as Answer).todos
    }

    func closeTodo(id: Int, tradeId: Int?, dropped: Bool = false) async throws -> TodoItem {
        struct Body: Encodable { let tradeId: Int?; let dropped: Bool }
        return try await post("/todos/\(id)/close", body: Body(tradeId: tradeId, dropped: dropped), authenticated: true)
    }
}
