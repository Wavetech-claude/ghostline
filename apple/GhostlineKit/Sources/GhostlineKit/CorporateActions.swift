import Foundation

// Corporate actions waiting for the owner in the real book: a split or a cash dividend the simulated books already
// took. Confirming writes it into the ledger (a dividend with the amount the broker paid); dismissing says the broker
// does not show it.

public struct PendingAction: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let kind: String             // forward_split | reverse_split | cash_dividend
    public let ticker: String
    public let exDate: String
    public let payableDate: String?
    public let shares: String
    public let amount: String?          // a dividend's expected cash
    public let newRate: String?
    public let oldRate: String?
    public let text: String

    public var isDividend: Bool { kind == "cash_dividend" }
}

public struct PendingActions: Codable, Sendable, Equatable {
    public let pending: [PendingAction]
}

public extension APIClient {
    func pendingActions() async throws -> [PendingAction] {
        let r: PendingActions = try await get("/corporate-actions", authenticated: true)
        return r.pending
    }

    /// The broker shows it: written into the real book. A dividend may carry the amount the broker actually paid.
    func confirmAction(id: String, amount: String? = nil) async throws {
        struct Body: Encodable { let amount: String? }
        struct Ack: Decodable { let status: String }
        _ = try await post("/corporate-actions/\(id)/confirm", body: Body(amount: amount), authenticated: true) as Ack
    }

    func dismissAction(id: String) async throws {
        struct Empty: Encodable {}
        struct Ack: Decodable { let status: String }
        _ = try await post("/corporate-actions/\(id)/dismiss", body: Empty(), authenticated: true) as Ack
    }
}
