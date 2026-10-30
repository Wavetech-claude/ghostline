import Foundation
import GhostlineKit
import Observation

/// The ledger as the app sees it: books, the portfolio, history, the risk rules. Every load says when it fails.
@MainActor
@Observable
final class LedgerModel {
    let client: APIClient
    private(set) var books: [BookSummary] = []
    private(set) var portfolio: Portfolio?
    private(set) var history: History?
    private(set) var rules: RiskRules?
    private(set) var error: String?
    private(set) var busy = false
    var filters = TradeFilters()
    var showingTradeForm = false      // the trade sheet, opened from History or the menu bar (⌘N)
    var showingCashFlow = false       // the cash sheet, opened from Account or the menu bar (⇧⌘N)
    var cashFlowBook: String?         // the book the cash sheet opens on (AI trader, options practice)
    var prefillTodo: TodoItem?        // "I Placed It": the trade sheet opens filled from this to-do item

    func logPlaced(_ todo: TodoItem) {
        prefillTodo = todo
        showingTradeForm = true
    }

    func addMoney(to book: String) {
        cashFlowBook = book
        showingCashFlow = true
    }

    init(client: APIClient) { self.client = client }

    func loadBooks() async { await run { self.books = try await self.client.books() } }
    func loadPortfolio() async { await run { self.portfolio = try await self.client.portfolio() } }
    func loadHistory() async { await run { self.history = try await self.client.history(self.filters) } }
    func loadRules() async { await run { self.rules = try await self.client.riskRules() } }

    func addCashFlow(book: String, amount: Decimal, at: Date, note: String?) async -> Bool {
        await run {
            _ = try await self.client.addCashFlow(CashFlowInput(book: book, amount: "\(amount)", at: Stamp.string(at),
                                                                 note: note))
            self.books = try await self.client.books()
            self.portfolio = try await self.client.portfolio()
        }
    }

    func addTrade(_ input: TradeInput, attachment: (Data, String, String)?) async -> TradeRow? {
        var made: TradeRow?
        _ = await run {
            let row = try await self.client.addTrade(input)
            if let (data, name, type) = attachment {
                _ = try await self.client.uploadAttachment(tradeId: row.id, data: data, filename: name, contentType: type)
            }
            made = row
            self.history = try await self.client.history(self.filters)
            self.portfolio = try await self.client.portfolio()
            self.books = try await self.client.books()
        }
        return made
    }

    /// Marks a to-do item placed, linked to the real trade that closed it.
    func closeTodo(_ todo: TodoItem, tradeId: Int) async {
        _ = await run { _ = try await self.client.closeTodo(id: todo.id, tradeId: tradeId) }
    }

    func saveRules(_ inputs: [RuleInput]) async -> Bool {
        await run { self.rules = try await self.client.saveRiskRules(inputs) }
    }

    var rulesComplete: Bool { rules?.complete ?? false }

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

/// Shows money strings from the backend without ever going through Double.
enum Show {
    static func money(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "—" }
        return Money.format(d)
    }

    static func signedMoney(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "—" }
        return (d > 0 ? "+" : "") + Money.format(d)
    }

    static func percent(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "—" }
        return "\(d.formatted(.number.precision(.fractionLength(0...2))))%"
    }

    /// A decimal string without trailing zeros, for a form field: "100.0500" → "100.05".
    static func plainNumber(_ s: String) -> String {
        guard let d = Decimal(string: s) else { return s }
        return NSDecimalNumber(decimal: d).stringValue
    }

    static func quantity(_ s: String) -> String {
        guard let d = Decimal(string: s) else { return s }
        return d.formatted(.number.precision(.fractionLength(0...6)))
    }

    static func isGain(_ s: String?) -> Bool? {
        guard let s, let d = Decimal(string: s), d != 0 else { return nil }
        return d > 0
    }
}
