import GhostlineKit
import SwiftUI

// Correcting mistakes, the way the competitor audit found real apps do it, without ever deleting:
// - the real book (typed in by you): Edit any field, or Void (kept, struck through, out of every number), Unvoid;
// - a practice order: Replace an open limit or stop order;
// - the practice, AI and options books: Reset, which voids their entries and cancels their open orders.

/// The real book's entries one by one: every trade and every deposit or withdrawal, to edit, void or unvoid.
struct RealEntriesView: View {
    @Environment(LedgerModel.self) private var ledger
    @State private var editingTrade: TradeRow?
    @State private var editingFlow: CashFlowRow?
    @State private var voiding: VoidTarget?

    var body: some View {
        @Bindable var ledger = ledger
        List {
            Section {
                Toggle("Show Voided", isOn: $ledger.showVoided)
            } footer: {
                Text("Swipe an entry to edit or void it. A voided entry is kept, struck through, and left out of every number; unvoid brings it back.")
            }
            Section("Trades") {
                ForEach(ledger.entries) { t in
                    NavigationLink { TradeEntryView(tradeId: t.id) } label: { TradeEntryRow(trade: t) }
                        .swipeActions { tradeActions(t) }
                        .contextMenu { tradeActions(t) }
                }
                if ledger.entries.isEmpty { Text("No real trades logged yet.").foregroundStyle(.secondary) }
            }
            Section("Deposits and Withdrawals") {
                ForEach(ledger.flows) { f in
                    NavigationLink { CashFlowEntryView(flowId: f.id) } label: { CashFlowEntryRow(flow: f) }
                        .swipeActions { flowActions(f) }
                        .contextMenu { flowActions(f) }
                }
                if ledger.flows.isEmpty { Text("No deposits yet.").foregroundStyle(.secondary) }
            }
            if let error = ledger.error { Text(error).font(.footnote).foregroundStyle(.loss) }
        }
        .navigationTitle("Real Book Entries")
        .task { await ledger.loadEntries() }
        .refreshable { await ledger.loadEntries() }
        .onChange(of: ledger.showVoided) { Task { await ledger.loadEntries() } }
        .sheet(item: $editingTrade) { TradeEditSheet(trade: $0) }
        .sheet(item: $editingFlow) { CashFlowEditSheet(flow: $0) }
        .voidPrompt($voiding)
    }

    @ViewBuilder
    private func tradeActions(_ t: TradeRow) -> some View {
        if t.isVoided {
            Button("Unvoid") { Task { _ = await ledger.setVoided(trade: t.id, false) } }.tint(.blue)
        } else {
            Button("Void", role: .destructive) { voiding = .trade(t.id, "\(t.side) \(Show.quantity(t.quantity)) \(t.ticker)") }
            if t.isEditable { Button("Edit") { editingTrade = t }.tint(.ghost) }
        }
    }

    @ViewBuilder
    private func flowActions(_ f: CashFlowRow) -> some View {
        if f.isVoided {
            Button("Unvoid") { Task { _ = await ledger.setVoided(flow: f.id, false) } }.tint(.blue)
        } else {
            Button("Void", role: .destructive) { voiding = .flow(f.id, Show.signedMoney(f.amount)) }
            Button("Edit") { editingFlow = f }.tint(.ghost)
        }
    }
}

struct TradeEntryRow: View {
    let trade: TradeRow

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(trade.ticker).font(.headline)
                Text(trade.side.replacingOccurrences(of: "_", with: " ")).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(Show.quantity(trade.quantity)) at \(Show.money(trade.fill))").monospacedDigit()
            }
            .strikethrough(trade.isVoided)
            Text([Stamp.short(trade.at), trade.broker, trade.lane].compactMap { $0 }.joined(separator: " · "))
                .font(.caption).foregroundStyle(.secondary)
            if let reason = trade.voidReason { Text("Voided: \(reason)").font(.caption2).foregroundStyle(.loss) }
        }
        .foregroundStyle(trade.isVoided ? .secondary : .primary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(trade.isVoided ? "Voided. \(trade.side) \(trade.ticker)" : "\(trade.side) \(trade.ticker)")
    }
}

struct CashFlowEntryRow: View {
    let flow: CashFlowRow

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(Decimal(string: flow.amount).map { $0 >= 0 } ?? true ? "Deposit" : "Withdrawal")
                Spacer()
                Text(Show.signedMoney(flow.amount)).monospacedDigit()
            }
            .strikethrough(flow.isVoided)
            Text([Stamp.short(flow.at), flow.note].compactMap { $0 }.joined(separator: " · "))
                .font(.caption).foregroundStyle(.secondary)
            if let reason = flow.voidReason { Text("Voided: \(reason)").font(.caption2).foregroundStyle(.loss) }
        }
        .foregroundStyle(flow.isVoided ? .secondary : .primary)
        .accessibilityElement(children: .combine)
    }
}

/// One trade with every change made to it.
struct TradeEntryView: View {
    @Environment(SessionModel.self) private var session
    let tradeId: Int
    @State private var detail: TradeDetail?
    @State private var error: String?

    var body: some View {
        List {
            if let d = detail {
                Section {
                    LabeledContent("Ticker", value: d.ticker)
                    LabeledContent("Side", value: d.side.replacingOccurrences(of: "_", with: " "))
                    LabeledContent("Quantity", value: Show.quantity(d.quantity))
                    LabeledContent("Price", value: Show.money(d.fill))
                    LabeledContent("Fees", value: Show.money(d.fees))
                    LabeledContent("When", value: Stamp.short(d.at))
                    if let b = d.broker { LabeledContent("Broker", value: b) }
                    if let s = d.optionSymbol { LabeledContent("Option", value: s) }
                    if let n = d.note { Text(n).font(.callout) }
                }
                if let reason = d.voidReason {
                    Section("Voided") { Text(reason).foregroundStyle(.loss) }
                }
                EditHistorySection(edits: d.edits ?? [])
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Trade")
        .task { await load() }
    }

    private func load() async {
        do { detail = try await session.client.trade(id: tradeId) } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

/// One deposit or withdrawal with every change made to it.
struct CashFlowEntryView: View {
    @Environment(SessionModel.self) private var session
    let flowId: Int
    @State private var detail: CashFlowDetail?
    @State private var error: String?

    var body: some View {
        List {
            if let d = detail {
                Section {
                    LabeledContent("Amount", value: Show.signedMoney(d.amount))
                    LabeledContent("When", value: Stamp.short(d.at))
                    if let n = d.note { Text(n).font(.callout) }
                }
                if let reason = d.voidReason {
                    Section("Voided") { Text(reason).foregroundStyle(.loss) }
                }
                EditHistorySection(edits: d.edits)
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Deposit")
        .task { await load() }
    }

    private func load() async {
        do { detail = try await session.client.cashFlow(id: flowId) } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct EditHistorySection: View {
    let edits: [LedgerEdit]

    var body: some View {
        Section("Changes") {
            ForEach(edits) { e in
                VStack(alignment: .leading, spacing: 2) {
                    Text(line(e)).font(.callout)
                    Text(Stamp.short(e.at)).font(.caption).foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
            }
            if edits.isEmpty { Text("Never changed.").foregroundStyle(.secondary) }
        }
    }

    private func line(_ e: LedgerEdit) -> String {
        switch e.action {
        case "void": return "Voided: \(e.reason ?? "")"
        case "unvoid": return "Unvoided"
        default:
            let name = (e.field ?? "").replacingOccurrences(of: "_", with: " ")
            return "\(name.capitalized): \(e.old ?? "empty") → \(e.new ?? "empty")"
        }
    }
}

/// Edit a real-book trade: every field, checked like a new trade; the old values stay in its history.
struct TradeEditSheet: View {
    @Environment(LedgerModel.self) private var ledger
    @Environment(\.dismiss) private var dismiss
    let trade: TradeRow
    @State private var ticker = ""
    @State private var side = "buy"
    @State private var quantity = ""
    @State private var fill = ""
    @State private var fees = ""
    @State private var at = Date()
    @State private var broker = ""
    @State private var note = ""
    @State private var lane = ""
    @State private var problem: String?

    private var isOption: Bool { trade.asset == "option" }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Ticker", text: $ticker)
                    #if os(iOS)
                    .textInputAutocapitalization(.characters)
                    #endif
                Picker("Side", selection: $side) {
                    if isOption {
                        Text("Sell to Open").tag("sell_to_open")
                        Text("Buy to Close").tag("buy_to_close")
                    } else {
                        Text("Buy").tag("buy")
                        Text("Sell").tag("sell")
                    }
                }
                number("Quantity", $quantity)
                number("Price", $fill)
                number("Fees", $fees)
                DatePicker("When", selection: $at)
                TextField("Broker", text: $broker)
                Picker("Lane", selection: $lane) {
                    Text("None").tag("")
                    Text("Long Term").tag("long")
                    Text("Swing").tag("swing")
                    Text("Sell Puts").tag("puts")
                    Text("Day").tag("day")
                }
                TextField("Note", text: $note, axis: .vertical)
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle("Edit Trade")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }.disabled(ledger.busy)
                }
            }
        }
        .onAppear(perform: fillIn)
    }

    private func number(_ label: String, _ value: Binding<String>) -> some View {
        LabeledContent(label) {
            TextField(label, text: value)
                .multilineTextAlignment(.trailing)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
        }
    }

    private func fillIn() {
        ticker = trade.ticker
        side = trade.side
        quantity = Show.plainNumber(trade.quantity)
        fill = Show.plainNumber(trade.fill)
        fees = Show.plainNumber(trade.fees)
        at = Stamp.date(trade.at) ?? Date()
        broker = trade.broker ?? ""
        note = trade.note ?? ""
        lane = trade.lane ?? ""
    }

    /// Only what changed is sent; an emptied broker, note or lane is cleared.
    private func changes() -> TradeChanges {
        var c = TradeChanges()
        let t = ticker.trimmingCharacters(in: .whitespaces).uppercased()
        if t != trade.ticker { c.ticker = t }
        if side != trade.side { c.side = side }
        if quantity != Show.plainNumber(trade.quantity) { c.quantity = quantity }
        if fill != Show.plainNumber(trade.fill) { c.fill = fill }
        if fees != Show.plainNumber(trade.fees) { c.fees = fees }
        if Stamp.string(at) != Stamp.string(Stamp.date(trade.at) ?? at) { c.at = Stamp.string(at) }
        for (key, value, old) in [("broker", broker, trade.broker), ("note", note, trade.note), ("lane", lane, trade.lane)]
        where value != (old ?? "") {
            if value.isEmpty { c.clear.insert(key) } else if key == "broker" { c.broker = value } else if key == "note" {
                c.note = value
            } else { c.lane = value }
        }
        return c
    }

    private func save() async {
        let c = changes()
        if c.isEmpty { dismiss(); return }
        if await ledger.editTrade(trade.id, c) { dismiss() } else { problem = ledger.error }
    }
}

/// Edit a real-book deposit or withdrawal.
struct CashFlowEditSheet: View {
    @Environment(LedgerModel.self) private var ledger
    @Environment(\.dismiss) private var dismiss
    let flow: CashFlowRow
    @State private var amount = ""
    @State private var at = Date()
    @State private var note = ""
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                LabeledContent("Amount") {
                    TextField("negative = withdrawal", text: $amount)
                        .multilineTextAlignment(.trailing)
                        #if os(iOS)
                        .keyboardType(.numbersAndPunctuation)
                        #endif
                }
                DatePicker("When", selection: $at)
                TextField("Note", text: $note)
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle("Edit Deposit")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }.disabled(ledger.busy)
                }
            }
        }
        .onAppear {
            amount = Show.plainNumber(flow.amount)
            at = Stamp.date(flow.at) ?? Date()
            note = flow.note ?? ""
        }
    }

    private func save() async {
        var c = CashFlowChanges()
        if amount != Show.plainNumber(flow.amount) { c.amount = amount.replacingOccurrences(of: ",", with: "") }
        if Stamp.string(at) != Stamp.string(Stamp.date(flow.at) ?? at) { c.at = Stamp.string(at) }
        if note != (flow.note ?? "") { c.note = note }
        if c == CashFlowChanges() { dismiss(); return }
        if await ledger.editCashFlow(flow.id, c) { dismiss() } else { problem = ledger.error }
    }
}

// ── void: always with a reason ─────────────────────────────────────────────────────────────────────────────
enum VoidTarget: Identifiable, Equatable {
    case trade(Int, String)
    case flow(Int, String)

    var id: String {
        switch self {
        case .trade(let i, _): "trade-\(i)"
        case .flow(let i, _): "flow-\(i)"
        }
    }

    var label: String {
        switch self {
        case .trade(_, let s), .flow(_, let s): s
        }
    }
}

private struct VoidPrompt: ViewModifier {
    @Environment(LedgerModel.self) private var ledger
    @Binding var target: VoidTarget?
    @State private var reason = ""

    func body(content: Content) -> some View {
        content.alert("Void \(target?.label ?? "")?", isPresented: Binding(get: { target != nil },
                                                                         set: { if !$0 { target = nil } })) {
            TextField("Why", text: $reason)
            Button("Void", role: .destructive) {
                let t = target
                let why = reason.trimmingCharacters(in: .whitespaces)
                reason = ""
                Task {
                    switch t {
                    case .trade(let id, _): _ = await ledger.setVoided(trade: id, true, reason: why)
                    case .flow(let id, _): _ = await ledger.setVoided(flow: id, true, reason: why)
                    case nil: break
                    }
                }
            }
            .disabled(reason.trimmingCharacters(in: .whitespaces).isEmpty)
            Button("Cancel", role: .cancel) { reason = "" }
        } message: {
            Text("It stays in your records, struck through, and leaves every number. You can unvoid it later.")
        }
    }
}

extension View {
    func voidPrompt(_ target: Binding<VoidTarget?>) -> some View { modifier(VoidPrompt(target: target)) }
}

// ── practice: replace an open order ────────────────────────────────────────────────────────────────────────
/// Replace an open limit, stop or stop-limit order: new prices, quantity or duration; the type stays.
struct ReplaceOrderSheet: View {
    @Environment(PracticeModel.self) private var practice
    @Environment(\.dismiss) private var dismiss
    let order: OrderRow
    @State private var quantity = ""
    @State private var limit = ""
    @State private var stop = ""
    @State private var tif = "day"
    @State private var problem: String?

    private var hasLimit: Bool { order.type == "limit" || order.type == "stop_limit" }
    private var hasStop: Bool { order.type == "stop" || order.type == "stop_limit" }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Order", value: "\(order.side.replacingOccurrences(of: "_", with: " ")) \(order.optionSymbol ?? order.ticker)")
                    LabeledContent("Type", value: order.type.replacingOccurrences(of: "_", with: "-"))
                }
                Section {
                    field("Quantity", $quantity)
                    if hasLimit { field("Limit Price", $limit) }
                    if hasStop { field("Stop Price", $stop) }
                    Picker("Good For", selection: $tif) {
                        Text("Day").tag("day")
                        Text("Until Cancelled").tag("gtc")
                    }
                } footer: {
                    Text("The new order passes every check a new order passes. If it is refused, your order stays as it was.")
                }
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle("Replace Order")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Replace") { Task { await save() } }.disabled(practice.busy)
                }
            }
        }
        .onAppear {
            quantity = Show.plainNumber(order.quantity)
            limit = order.limitPrice.map(Show.plainNumber) ?? ""
            stop = order.stopPrice.map(Show.plainNumber) ?? ""
            tif = order.tif
        }
    }

    private func field(_ label: String, _ value: Binding<String>) -> some View {
        LabeledContent(label) {
            TextField(label, text: value)
                .multilineTextAlignment(.trailing)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
        }
    }

    private func save() async {
        var input = ReplaceInput()
        if quantity != Show.plainNumber(order.quantity) { input.quantity = quantity }
        if hasLimit, limit != (order.limitPrice.map(Show.plainNumber) ?? "") { input.limitPrice = limit }
        if hasStop, stop != (order.stopPrice.map(Show.plainNumber) ?? "") { input.stopPrice = stop }
        if tif != order.tif { input.tif = tif }
        if input == ReplaceInput() { dismiss(); return }
        if await practice.replace(order, input) != nil { dismiss() } else { problem = practice.error }
    }
}

// ── reset a simulated book ─────────────────────────────────────────────────────────────────────────────────
/// "Start Over" for the practice, AI or options book, as a broker's paper account is reset.
struct ResetBookSection: View {
    @Environment(PracticeModel.self) private var practice
    let book: String
    let name: String
    var lastReset: String?
    var onDone: () async -> Void = {}
    @State private var asking = false
    @State private var deposit = ""
    @State private var result: String?

    var body: some View {
        Section {
            Button("Start \(name) Over", role: .destructive) { asking = true }
            if let result { Text(result).font(.footnote).foregroundStyle(.secondary) }
        } footer: {
            Text(lastReset.map { "Last started over on \($0)." } ?? "Starting over keeps every old trade and deposit, voided; learning is not affected.")
        }
        .alert("Start \(name) over?", isPresented: $asking) {
            TextField("Starting deposit (optional)", text: $deposit)
            Button("Start Over", role: .destructive) { Task { await reset() } }
            Button("Cancel", role: .cancel) { deposit = "" }
        } message: {
            Text(book == "ai"
                 ? "Every AI trader trade and deposit is voided, and the S&P 500 ghost with them; open orders are cancelled. They stay under Show Voided. What the app has learned is not affected."
                 : "Every trade and deposit in this book is voided and its open orders are cancelled. They stay under Show Voided. What the app has learned is not affected.")
        }
    }

    private func reset() async {
        let amount = deposit.replacingOccurrences(of: ",", with: "").trimmingCharacters(in: .whitespaces)
        deposit = ""
        if let r = await practice.reset(book: book, deposit: amount.isEmpty ? nil : amount) {
            result = "Started over: \(r.trades) trades and \(r.cashFlows) deposits voided, \(r.ordersCancelled) orders cancelled."
            await onDone()
        } else {
            result = practice.error
        }
    }
}
