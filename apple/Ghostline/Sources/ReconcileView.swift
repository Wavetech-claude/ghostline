import GhostlineKit
import SwiftUI

/// Reconcile (the owner's design, 2026-10-30), month by month:
/// - what the app suggested against what the owner did: took it, said no, let it lapse, or still open, plus the
///   owner's own calls;
/// - each with its paper result (what following it did or would have done) and, when taken, its realized P&L;
/// - the real book against the broker's statement.
struct ReconcileView: View {
    @Environment(SessionModel.self) private var session
    @State private var month = ReconcileView.thisMonth()
    @State private var report: Reconciliation?
    @State private var problem: String?
    @State private var entering = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                monthBar.padding(.horizontal, 16)
                if let report {
                    ReconcileSections(report: report,
                                      decline: { row in Task { await act { try await session.client.decline(kind: row.kind, refId: row.id, reason: nil) } } },
                                      undo: { row in Task { await act { try await session.client.undoDecline(kind: row.kind, refId: row.id) } } },
                                      enterStatement: { entering = true })
                } else if let problem {
                    Text(problem).foregroundStyle(.secondary).padding()
                } else {
                    ProgressView().frame(maxWidth: .infinity, minHeight: 200)
                }
            }
            .padding(.vertical, 12)
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Reconcile")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .task(id: month) { await load() }
        .refreshable { await load() }
        .sheet(isPresented: $entering) { StatementSheet { await load() } }
    }

    private var monthBar: some View {
        HStack {
            Button { month = Self.shift(month, by: -1) } label: { Image(systemName: "chevron.left") }
                .accessibilityLabel("Previous month")
            Spacer()
            Text(Self.title(month)).font(.headline)
            Spacer()
            Button { month = Self.shift(month, by: 1) } label: { Image(systemName: "chevron.right") }
                .accessibilityLabel("Next month")
                .disabled(month == Self.thisMonth())
        }
        .buttonStyle(.bordered)
    }

    private func load() async {
        do {
            report = try await session.client.reconciliation(month: month)
            problem = nil
        } catch {
            report = nil
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func act(_ call: () async throws -> Void) async {
        do {
            try await call()
            await load()
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    static func thisMonth(_ now: Date = Date()) -> String {
        let c = Calendar.current.dateComponents([.year, .month], from: now)
        return String(format: "%04d-%02d", c.year ?? 2026, c.month ?? 1)
    }

    static func shift(_ month: String, by n: Int) -> String {
        let parts = month.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 2 else { return month }
        let total = parts[0] * 12 + (parts[1] - 1) + n
        return String(format: "%04d-%02d", total / 12, total % 12 + 1)
    }

    static func title(_ month: String) -> String {
        // the first of the month, read and written in UTC, so no time zone moves it into the month before
        guard let d = try? Date(month + "-01", strategy: .iso8601.year().month().day()) else { return month }
        var style = Date.FormatStyle(date: .omitted, time: .omitted).month(.wide).year()
        style.timeZone = TimeZone(identifier: "UTC") ?? .current
        return d.formatted(style)
    }
}

/// The month's sections, drawn from the report (ReconcileView loads it). Apart from the scroll view, so a check can
/// draw them.
struct ReconcileSections: View {
    let report: Reconciliation
    let decline: (SuggestionRow) -> Void
    let undo: (SuggestionRow) -> Void
    let enterStatement: () -> Void

    private static let groups = [("took", "You Took"), ("declined", "You Said No"), ("lapsed", "Lapsed"),
                                 ("open", "Still Open")]

    var body: some View {
        GhostCard(title: "This Month") {
            SummaryLine(title: "You took", outcome: report.summary.took, paper: .followed, showRealized: true)
            SummaryLine(title: "You said no", outcome: report.summary.declined, paper: .passedOver, showRealized: false)
            SummaryLine(title: "Lapsed", outcome: report.summary.lapsed, paper: .passedOver, showRealized: false)
            SummaryLine(title: "Still open", outcome: report.summary.open, paper: .none, showRealized: false)
            SummaryLine(title: "Your own calls", outcome: report.summary.ownCalls, paper: .none, showRealized: true)
            Text("Paper results are each idea's settled result against the S&P 500 after costs; realized P&L is to date.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        GhostCard(title: "Against Your Broker") { BrokerSummary(check: report.broker, enter: enterStatement) }
        ForEach(Self.groups, id: \.0) { outcome, title in
            let rows = report.suggestions.filter { $0.outcome == outcome }
            if !rows.isEmpty {
                GhostCard(title: title) {
                    ForEach(rows, id: \.key) { row in
                        SuggestionLine(row: row, decline: decline, undo: undo)
                        if row.key != rows.last?.key { Divider() }
                    }
                }
            }
        }
        if !report.ownCalls.isEmpty {
            GhostCard(title: "Your Own Calls") {
                ForEach(report.ownCalls) { call in
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(call.ticker).font(.headline)
                            Text("\(Show.quantity(call.quantity)) at \(Show.money(call.fill)) · \(Stamp.day(call.at))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if let r = call.realized {
                            Text(Show.signedMoney(r)).font(.subheadline.weight(.semibold)).monospacedDigit()
                                .foregroundStyle(Show.tint(r))
                        }
                    }
                }
            }
        }
        if report.suggestions.isEmpty && report.ownCalls.isEmpty {
            Text("Nothing to reconcile in this month yet.").foregroundStyle(.secondary).padding(.horizontal, 20)
        }
    }
}

private struct SummaryLine: View {
    /// A followed idea's paper result reads as a gain or a loss. For one passed over, a loss on paper was avoided and a
    /// gain was missed, so it is said in words, not in gain and loss colours.
    enum Paper { case none, followed, passedOver }

    let title: String
    let outcome: OutcomeSummary
    let paper: Paper
    let showRealized: Bool

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.subheadline)
            Text("\(outcome.n)").font(.subheadline.weight(.semibold)).monospacedDigit()
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                if showRealized, let r = outcome.realized {
                    Text("\(Show.signedMoney(r)) realized").font(.caption.weight(.semibold)).foregroundStyle(Show.tint(r))
                }
                if let p = outcome.paperExcessAvg, let d = Decimal(string: p) {
                    switch paper {
                    case .followed:
                        Text("\(Show.signedPercent(p)) on paper").font(.caption).foregroundStyle(Show.tint(p))
                    case .passedOver:
                        Text("\(d < 0 ? "avoided" : "missed") \(Show.signedPercent(p)) on paper")
                            .font(.caption).foregroundStyle(.secondary)
                    case .none:
                        EmptyView()
                    }
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct SuggestionLine: View {
    let row: SuggestionRow
    let decline: (SuggestionRow) -> Void
    let undo: (SuggestionRow) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(row.ticker) · \(row.label)").font(.headline)
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    if let r = row.realized {
                        Text(Show.signedMoney(r)).font(.subheadline.weight(.semibold)).monospacedDigit()
                            .foregroundStyle(Show.tint(r))
                    }
                    if let p = row.paper?.excessPct {
                        Text("paper \(Show.signedPercent(p))").font(.caption).foregroundStyle(Show.tint(p))
                    }
                }
            }
            if let reason = row.reason { Text("“\(reason)”").font(.caption).italic() }
            if row.outcome == "open" {
                Button("Not for Me") { decline(row) }.buttonStyle(.bordered).controlSize(.small)
            } else if row.outcome == "declined" {
                Button("Undo") { undo(row) }.buttonStyle(.bordered).controlSize(.small)
            }
        }
    }

    private var detail: String {
        var parts = [Stamp.day(row.at)]
        if let t = row.taken { parts.append("bought \(Show.quantity(t.quantity)) at \(Show.money(t.fill))") }
        else if let e = row.suggested.entry { parts.append("suggested at \(Show.money(e))") }
        return parts.joined(separator: " · ")
    }
}

private struct BrokerSummary: View {
    let check: BrokerCheck?
    let enter: () -> Void

    var body: some View {
        if let check {
            Text("Statement of \(Stamp.day(check.asOf))").font(.subheadline).foregroundStyle(.secondary)
            if check.matches {
                Label("Shares and cash match", systemImage: "checkmark.seal.fill").foregroundStyle(.gain)
            } else {
                ForEach(check.shares.filter { !$0.match }) { line in
                    Figure(label: line.ticker, value: "app \(line.app) · broker \(line.broker)", tint: .loss)
                }
                if !check.cash.match {
                    Figure(label: "Cash", value: "app \(Show.money(check.cash.app)) · broker \(Show.money(check.cash.broker))",
                           tint: .loss)
                }
                Text("Fix the difference in the app (Portfolio → History) or check the statement.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(check.note).font(.caption2).foregroundStyle(.tertiary)
        } else {
            Text("Type in your broker's statement to check the real book's shares and cash.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        Button("Enter a Statement", action: enter).buttonStyle(.bordered)
    }
}

/// A broker statement, typed in: its date, the cash, and each stock holding.
struct StatementSheet: View {
    @Environment(SessionModel.self) private var session
    @Environment(\.dismiss) private var dismiss
    let saved: () async -> Void
    @State private var asOf = Date()
    @State private var cash = ""
    @State private var holdings: [StatementInput.Holding] = [StatementInput.Holding(ticker: "", quantity: "")]
    @State private var problem: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    DatePicker("Statement date", selection: $asOf, in: ...Date(), displayedComponents: .date)
                    LabeledContent("Cash") {
                        TextField("0.00", text: $cash).multilineTextAlignment(.trailing)
                            #if os(iOS)
                            .keyboardType(.decimalPad)
                            #endif
                    }
                }
                Section {
                    ForEach(holdings.indices, id: \.self) { i in
                        HStack {
                            TextField("Ticker", text: $holdings[i].ticker)
                                #if os(iOS)
                                .textInputAutocapitalization(.characters)
                                #endif
                            TextField("Shares", text: $holdings[i].quantity).multilineTextAlignment(.trailing)
                                #if os(iOS)
                                .keyboardType(.decimalPad)
                                #endif
                        }
                    }
                    .onDelete { holdings.remove(atOffsets: $0) }
                    Button("Add a Holding") { holdings.append(StatementInput.Holding(ticker: "", quantity: "")) }
                } header: {
                    Text("Stock Holdings")
                } footer: {
                    Text("Type what the statement shows. Options are not compared yet.")
                }
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            }
            .navigationTitle("Broker Statement")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Compare") { Task { await save() } }.disabled(Decimal(string: cash) == nil || busy)
                }
            }
        }
    }

    private func save() async {
        busy = true
        defer { busy = false }
        let rows = holdings.filter { !$0.ticker.trimmingCharacters(in: .whitespaces).isEmpty }
        let day = Stamp.localDay(asOf)
        do {
            _ = try await session.client.addStatement(StatementInput(asOf: day, cash: cash, positions: rows))
            await saved()
            dismiss()
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}
