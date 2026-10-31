import GhostlineKit
import SwiftUI

/// "Invest this": any amount, a date, the book it goes into. The split comes back with every idea's share, price
/// and source, and the rule that refused a split. Approve it into practice, or onto the to-do list for real money.
struct InvestSheet: View {
    @Environment(SessionModel.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State private var amount = ""
    @State private var date = InvestSheet.nextWeekday()
    @State private var book = "practice"
    @State private var deposit = true
    @State private var plan: InvestPlan?
    @State private var busy = false
    @State private var problem: String?
    @State private var done: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Amount") {
                        TextField("any amount", text: $amount)
                            .multilineTextAlignment(.trailing)
                            #if os(iOS)
                            .keyboardType(.decimalPad)
                            #endif
                    }
                    DatePicker("On", selection: $date, in: Date()..., displayedComponents: .date)
                    Picker("Money", selection: $book) {
                        Text("Practice").tag("practice")
                        Text("Real").tag("real")
                    }
                    .pickerStyle(.segmented)
                } footer: {
                    Text("Split across today's long-term and swing ideas, the best idea taking the biggest share, every split through your risk rules.")
                }
                if let plan {
                    Section("The Split") {
                        ForEach(plan.splits) { SplitRow(split: $0) }
                        if plan.splits.isEmpty { Text(plan.note ?? "Nothing to split into.").foregroundStyle(.secondary) }
                        if Decimal(string: plan.unallocated) ?? 0 > 0 {
                            LabeledContent("Stays as cash", value: Show.money(plan.unallocated))
                        }
                    }
                    if plan.status == "proposed", plan.splits.contains(where: \.passed) {
                        Section {
                            if plan.book == "practice" {
                                Toggle("Add \(Show.money(plan.amount)) to the practice account first", isOn: $deposit)
                                Button("Approve into Practice") { Task { await approve("practice") } }
                                    .buttonStyle(.borderedProminent)
                            } else {
                                Button("Put on My To-Do List") { Task { await approve("real") } }
                                    .buttonStyle(.borderedProminent)
                            }
                        } footer: {
                            Text(plan.book == "practice"
                                 ? "Practice orders are placed at the open on \(Stamp.day(plan.forDate))."
                                 : "The app never places real trades. Each split becomes a to-do item; tap “I Placed It” after you place it.")
                        }
                    }
                }
                if let done { Label(done, systemImage: "checkmark.circle").foregroundStyle(.gain) }
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle("Invest This")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(done == nil ? "Cancel" : "Done") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Split It") { Task { await split() } }
                        .disabled(Decimal(string: amount).map { $0 <= 0 } ?? true || busy)
                }
            }
        }
    }

    static func nextWeekday(from now: Date = Date()) -> Date {
        var d = Calendar.current.date(byAdding: .day, value: 1, to: now) ?? now
        while Calendar.current.isDateInWeekend(d) { d = Calendar.current.date(byAdding: .day, value: 1, to: d) ?? d }
        return d
    }

    private func split() async {
        busy = true
        defer { busy = false }
        let day = Stamp.localDay(date)            // the day picked, not its UTC day
        do {
            plan = try await session.client.invest(amount: amount, forDate: day, book: book)
            problem = nil
            done = nil
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func approve(_ to: String) async {
        guard let plan else { return }
        busy = true
        defer { busy = false }
        do {
            self.plan = try await session.client.approvePlan(id: plan.id, to: to, deposit: deposit)
            done = to == "practice" ? "Approved into practice." : "On your to-do list."
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct SplitRow: View {
    let split: InvestSplit

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(split.ticker).font(.headline)
                Text("\(split.lane) · \(split.module.replacingOccurrences(of: "_", with: " "))")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(Show.money(split.dollars)).monospacedDigit()
                    .foregroundStyle(split.passed ? Color.primary : Color.secondary)
            }
            if split.passed, let shares = split.shares, let price = split.price {
                Text("\(Show.quantity(shares)) shares at \(Show.money(price)) · stop \(Show.money(split.stop)) · risk \(Show.money(split.maxLoss))")
                    .font(.caption).foregroundStyle(.secondary)
                if let src = split.priceSource { Text(src).font(.caption2).foregroundStyle(.tertiary) }
            } else {
                Label(split.why, systemImage: "hand.raised").font(.caption).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Real-money trades waiting for the owner to place them at his broker.
struct TodoListView: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger
    @State private var items: [TodoItem] = []
    @State private var loaded = false
    @State private var error: String?

    var body: some View {
        List {
            ForEach(items) { t in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("\(t.side.replacingOccurrences(of: "_", with: " ").capitalized) \(Show.quantity(t.quantity)) \(t.optionSymbol ?? t.ticker)")
                            .font(.headline)
                        Spacer()
                        if let p = t.limitPrice { Text("about \(Show.money(p))").font(.caption).foregroundStyle(.secondary) }
                    }
                    if let note = t.note { Text(note).font(.caption).foregroundStyle(.secondary) }
                    Button("I Placed It") { ledger.logPlaced(t) }.buttonStyle(.bordered)
                }
                .swipeActions {
                    Button("Drop", role: .destructive) { Task { await drop(t) } }
                }
            }
            if items.isEmpty && loaded {
                ContentUnavailableView("Nothing to place", systemImage: "checklist",
                                       description: Text("Real-money splits and copies you approve wait here until you place them."))
            }
            if let error { Text(error).foregroundStyle(.secondary) }
        }
        .navigationTitle("To Place at Your Broker")
        .task { await load() }
        .refreshable { await load() }
        .onChange(of: ledger.showingTradeForm) { if !ledger.showingTradeForm { Task { await load() } } }
    }

    private func load() async {
        do {
            items = try await session.client.todos()
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
        loaded = true
    }

    private func drop(_ t: TodoItem) async {
        _ = try? await session.client.closeTodo(id: t.id, tradeId: nil, dropped: true)
        await load()
    }
}
