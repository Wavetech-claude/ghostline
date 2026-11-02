import GhostlineKit
import SwiftUI

/// Recurring buys in practice: each plan, when it runs next, and what its last runs did; pause, resume or delete.
struct RecurringView: View {
    @Environment(SessionModel.self) private var session
    @State private var plans: [RecurringPlan] = []
    @State private var loaded = false
    @State private var adding = false
    @State private var error: String?

    var body: some View {
        List {
            Section {
                ForEach(plans) { p in
                    NavigationLink { RecurringDetail(plan: p) } label: { row(p) }
                        .swipeActions {
                            Button("Delete", role: .destructive) { Task { await delete(p) } }
                        }
                }
                if plans.isEmpty && loaded {
                    ContentUnavailableView("No recurring buys", systemImage: "arrow.triangle.2.circlepath",
                                           description: Text("Buy a set amount of a stock every day, week, two weeks or month, in practice."))
                }
            } footer: {
                Text("Each run is a practice market order on a trading day, about 06:50 PT, sized in fractional shares at the ask. It passes the same risk rules as any order. A day the market is closed moves to the next session.")
            }
            if let error { Text(error).foregroundStyle(.secondary) }
        }
        .navigationTitle("Recurring Buys")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { adding = true } label: { Label("Add Recurring Buy", systemImage: "plus") }
            }
        }
        .sheet(isPresented: $adding, onDismiss: { Task { await load() } }) { AddRecurringSheet() }
        .task { await load() }
        .refreshable { await load() }
    }

    private func row(_ p: RecurringPlan) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text("\(Show.money(p.dollars)) of \(p.ticker)").font(.headline)
                Spacer()
                Toggle("On", isOn: Binding(get: { p.isActive }, set: { on in Task { await set(p, on) } }))
                    .labelsHidden()
            }
            Text(p.scheduleText).font(.callout)
            Text(p.isActive ? (p.nextRun.map { "Next: \(Stamp.day($0))" } ?? "No run in the next ten weeks") : "Paused")
                .font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }

    private func load() async {
        do {
            plans = try await session.client.recurringPlans().plans
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
        loaded = true
    }

    private func set(_ p: RecurringPlan, _ on: Bool) async {
        do {
            _ = try await session.client.setRecurring(id: p.id, active: on)
            await load()
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func delete(_ p: RecurringPlan) async {
        do {
            try await session.client.deleteRecurring(id: p.id)
            await load()
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

/// One plan's runs: placed with the order's fill, or skipped with the reason.
struct RecurringDetail: View {
    let plan: RecurringPlan

    var body: some View {
        List {
            Section {
                LabeledContent("Amount", value: Show.money(plan.dollars))
                LabeledContent("Schedule", value: plan.scheduleText)
                LabeledContent("Since", value: Stamp.day(plan.startsOn))
            }
            Section("Last Runs") {
                ForEach(plan.runs) { r in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(Stamp.day(r.day))
                            Spacer()
                            Text(r.status == "placed" ? (r.orderStatus ?? "placed").capitalized : "Skipped")
                                .font(.caption).foregroundStyle(r.status == "placed" ? Color.secondary : Color.loss)
                        }
                        if let q = r.quantity, let f = r.fillPrice {
                            Text("\(Show.quantity(q)) shares at \(Show.money(f))").font(.caption).foregroundStyle(.secondary)
                        }
                        if let why = r.reason { Text(why).font(.caption).foregroundStyle(.secondary) }
                    }
                }
                if plan.runs.isEmpty { Text("No run yet.").foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("\(plan.ticker) Recurring")
    }
}

struct AddRecurringSheet: View {
    @Environment(SessionModel.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State private var ticker = ""
    @State private var dollars = ""
    @State private var every = "week"
    @State private var weekday = 0
    @State private var monthDay = 1
    @State private var problem: String?
    @State private var busy = false

    private var valid: Bool {
        !ticker.trimmingCharacters(in: .whitespaces).isEmpty && (Decimal(string: dollars).map { $0 > 0 } ?? false)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Ticker", text: $ticker)
                        #if os(iOS)
                        .textInputAutocapitalization(.characters)
                        #endif
                        .autocorrectionDisabled()
                    LabeledContent("Amount ($)") {
                        TextField("0", text: $dollars)
                            .multilineTextAlignment(.trailing)
                            #if os(iOS)
                            .keyboardType(.decimalPad)
                            #endif
                    }
                }
                Section {
                    Picker("Every", selection: $every) {
                        Text("Trading Day").tag("day")
                        Text("Week").tag("week")
                        Text("Two Weeks").tag("two_weeks")
                        Text("Month").tag("month")
                    }
                    if every == "week" || every == "two_weeks" {
                        Picker("On", selection: $weekday) {
                            ForEach(Array(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].enumerated()), id: \.offset) {
                                Text($0.element).tag($0.offset)
                            }
                        }
                    }
                    if every == "month" {
                        Stepper("On the \(monthDay)", value: $monthDay, in: 1...31)
                    }
                } footer: {
                    Text(every == "month" && monthDay > 28 ? "A shorter month runs on its last day." : "It starts today. A day the market is closed moves to the next session.")
                }
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle("Recurring Buy")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { Task { await add() } }.disabled(!valid || busy)
                }
            }
        }
    }

    private func add() async {
        busy = true
        defer { busy = false }
        let input = RecurringInput(ticker: ticker.trimmingCharacters(in: .whitespaces).uppercased(), dollars: dollars,
                                   every: every, weekday: weekday, monthDay: monthDay)
        do {
            _ = try await session.client.createRecurring(input)
            dismiss()
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}
