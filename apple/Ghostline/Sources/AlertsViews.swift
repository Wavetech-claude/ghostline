import GhostlineKit
import SwiftUI

/// Price alerts: what each one watches, whether it fired and what it said; on, off, or gone.
struct AlertsView: View {
    @Environment(SessionModel.self) private var session
    var ticker: String?
    @State private var alerts: [PriceAlert] = []
    @State private var loaded = false
    @State private var adding = false
    @State private var error: String?

    var body: some View {
        List {
            ForEach(alerts) { a in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(a.ticker).font(.headline)
                        Text(a.rule).font(.callout)
                        Spacer()
                        Toggle("On", isOn: Binding(get: { a.status == "active" },
                                                   set: { on in Task { await set(a, on) } }))
                            .labelsHidden()
                    }
                    if let m = a.message, a.status != "active" {
                        Text("Fired \(Stamp.short(a.triggeredAt)): \(m)").font(.caption).foregroundStyle(.secondary)
                    } else if let checked = a.lastChecked {
                        Text("Checked \(Stamp.short(checked))").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                .swipeActions {
                    Button("Delete", role: .destructive) { Task { await delete(a) } }
                }
            }
            if alerts.isEmpty && loaded {
                ContentUnavailableView("No alerts", systemImage: "bell",
                                       description: Text("Add one for a price, a day's move, a moving average or earnings."))
            }
            if let error { Text(error).foregroundStyle(.secondary) }
        }
        .navigationTitle(ticker.map { "\($0) Alerts" } ?? "Price Alerts")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { adding = true } label: { Label("Add Alert", systemImage: "plus") }
            }
        }
        .sheet(isPresented: $adding, onDismiss: { Task { await load() } }) { AddAlertSheet(ticker: ticker ?? "") }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do {
            alerts = try await session.client.alerts(ticker: ticker)
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
        loaded = true
    }

    private func set(_ a: PriceAlert, _ on: Bool) async {
        _ = try? await session.client.setAlert(id: a.id, active: on)
        await load()
    }

    private func delete(_ a: PriceAlert) async {
        try? await session.client.deleteAlert(id: a.id)
        await load()
    }
}

struct AddAlertSheet: View {
    @Environment(SessionModel.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State var ticker: String
    @State private var kind = "above"
    @State private var value = ""
    @State private var problem: String?

    private var valueLabel: String {
        switch kind {
        case "pct_move": "Percent"
        case "ma_cross": "Days in the average"
        case "earnings": "Days before earnings"
        default: "Price"
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Ticker", text: $ticker)
                    #if os(iOS)
                    .textInputAutocapitalization(.characters)
                    #endif
                    .autocorrectionDisabled()
                Picker("When", selection: $kind) {
                    Text("Price Goes Above").tag("above")
                    Text("Price Goes Below").tag("below")
                    Text("Moves in a Day").tag("pct_move")
                    Text("Crosses a Moving Average").tag("ma_cross")
                    Text("Earnings Are Coming").tag("earnings")
                }
                LabeledContent(valueLabel) {
                    TextField(kind == "ma_cross" ? "50" : "0", text: $value)
                        .multilineTextAlignment(.trailing)
                        #if os(iOS)
                        .keyboardType(.decimalPad)
                        #endif
                }
                if let problem { Text(problem).foregroundStyle(.red).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle("Add Alert")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { Task { await add() } }
                        .disabled(ticker.trimmingCharacters(in: .whitespaces).isEmpty || Decimal(string: value) == nil)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func add() async {
        do {
            _ = try await session.client.addAlert(ticker: ticker.trimmingCharacters(in: .whitespaces).uppercased(),
                                                  kind: kind, value: value)
            dismiss()
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

/// A month's statement for one book: deposits, withdrawals, trades, fees, realized P&L, cash at the end; shared as CSV
/// or PDF.
struct StatementsView: View {
    @Environment(SessionModel.self) private var session
    @State private var book = "real"
    @State private var month = StatementsView.months().first ?? ""
    @State private var statement: Statement?
    @State private var error: String?
    @State private var file: URL?
    @State private var exporting = false

    private let books = [("real", "Real"), ("practice", "Practice"), ("ai", "AI Trader"), ("options", "Options Practice"),
                         ("ghost", "S&P 500 Ghost")]

    var body: some View {
        List {
            Section {
                Picker("Book", selection: $book) { ForEach(books, id: \.0) { Text($0.1).tag($0.0) } }
                Picker("Month", selection: $month) { ForEach(Self.months(), id: \.self) { Text($0).tag($0) } }
            }
            if let s = statement {
                Section("Summary") {
                    LabeledContent("Deposits", value: Show.money(s.deposits))
                    LabeledContent("Withdrawals", value: Show.money(s.withdrawals))
                    LabeledContent("Fees", value: Show.money(s.fees))
                    LabeledContent("Realized P&L After Fees", value: Show.signedMoney(s.realizedPnl))
                    LabeledContent("Cash at Month End", value: Show.money(s.cashAtEnd))
                    LabeledContent("Dividends", value: s.dividends.map { Show.money($0) } ?? "Not tracked yet")
                }
                Section("Trades") {
                    ForEach(s.trades) { t in
                        Text("\(Stamp.day(t.at)) · \(t.side.replacingOccurrences(of: "_", with: " ")) \(Show.quantity(t.quantity)) \(t.optionSymbol ?? t.ticker) at \(Show.money(t.fill))")
                            .font(.callout)
                    }
                    if s.trades.isEmpty { Text("No trades this month.").foregroundStyle(.secondary) }
                }
                Section {
                    HStack {
                        Button("Export CSV") { Task { await export("csv") } }
                        Button("Export PDF") { Task { await export("pdf") } }
                    }
                    .buttonStyle(.bordered)
                    .disabled(exporting)
                    if let file {
                        ShareLink(item: file) { Label("Share \(file.lastPathComponent)", systemImage: "square.and.arrow.up") }
                    }
                }
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Statements")
        .task(id: book + month) { await load() }
    }

    /// This month and the eleven before it, newest first, as YYYY-MM.
    static func months(from now: Date = Date()) -> [String] {
        let cal = Calendar(identifier: .gregorian)
        return (0..<12).compactMap { i in
            guard let d = cal.date(byAdding: .month, value: -i, to: now) else { return nil }
            let c = cal.dateComponents([.year, .month], from: d)
            return String(format: "%04d-%02d", c.year ?? 0, c.month ?? 0)
        }
    }

    private func load() async {
        file = nil
        do {
            statement = try await session.client.statement(book: book, month: month)
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func export(_ format: String) async {
        exporting = true
        defer { exporting = false }
        do {
            let data = try await session.client.statementFile(book: book, month: month, format: format)
            let url = FileManager.default.temporaryDirectory.appending(path: "ghostline-\(book)-\(month).\(format)")
            try data.write(to: url, options: .atomic)
            file = url
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}
