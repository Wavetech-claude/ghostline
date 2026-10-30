import Foundation
import GhostlineKit
import Observation

/// The practice account as the app sees it: buying power, open orders, fills and refusals. Every refusal is shown
/// with the rule that refused it; nothing real moves.
@MainActor
@Observable
final class PracticeModel {
    let client: APIClient
    private(set) var account: PracticeAccount?
    private(set) var orders: [OrderRow] = []
    private(set) var error: String?
    private(set) var busy = false
    var showingTicket = false
    var ticketTicker = ""              // pre-fills the ticket when opened from a ticker page
    var ticketOption: OptionRow?       // pre-fills a sell-to-open put from the chain

    init(client: APIClient) { self.client = client }

    func load(book: String = "practice") async {
        await run {
            self.account = try await self.client.practiceAccount(book: book)
            self.orders = try await self.client.orders(book: book)
        }
    }

    /// Places the order and returns the row (filled, open or refused with its reason); nil when the call failed.
    func place(_ input: OrderInput) async -> OrderRow? {
        var made: OrderRow?
        _ = await run {
            made = try await self.client.placeOrder(input)
            self.account = try await self.client.practiceAccount(book: input.book)
            self.orders = try await self.client.orders(book: input.book)
        }
        return made
    }

    func cancel(_ order: OrderRow) async {
        await run {
            _ = try await self.client.cancelOrder(id: order.id)
            self.account = try await self.client.practiceAccount(book: order.book ?? "practice")
            self.orders = try await self.client.orders(book: order.book ?? "practice")
        }
    }

    func openTicket(ticker: String = "", option: OptionRow? = nil) {
        ticketTicker = ticker
        ticketOption = option
        showingTicket = true
    }

    @discardableResult
    private func run(_ work: @escaping @MainActor () async throws -> Void) async -> Bool {
        busy = true
        defer { busy = false }
        do {
            try await work()
            error = nil
            return true
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
            return false
        }
    }
}
