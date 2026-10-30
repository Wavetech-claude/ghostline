import GhostlineKit
import SwiftUI

/// Ideas in three lanes: long term, swings, sell puts. Each card says why this, why now, how much it would risk, and
/// when the rules refused it, which rule and why. Nothing here is hidden because it did not fit.
struct IdeasView: View {
    @Environment(SessionModel.self) private var session
    @Environment(PracticeModel.self) private var practice
    @State private var lane = "long"
    @State private var all: Ideas?
    @State private var regime: SignalRow?
    @State private var error: String?
    @State private var scanning = false
    @State private var investing = false

    private let lanes = [("long", "Long Term"), ("swing", "Swings"), ("puts", "Sell Puts")]

    private var shown: [IdeaRow] { (all?.ideas ?? []).filter { $0.lane == lane } }

    var body: some View {
        List {
            Section {
                Picker("Lane", selection: $lane) {
                    ForEach(lanes, id: \.0) { Text($0.1).tag($0.0) }
                }
                .pickerStyle(.segmented)
                .listRowSeparator(.hidden)
                if let regime, case .object(let ev) = regime.evidence, case .string(let name)? = ev["regime"] {
                    Label("Market regime: \(name.replacingOccurrences(of: "_", with: " "))", systemImage: "globe")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            if let all, let day = all.lastScan.day {
                Section {
                    ForEach(shown) { idea in
                        NavigationLink { IdeaDetailView(idea: idea) } label: { IdeaCard(idea: idea) }
                    }
                    if shown.isEmpty {
                        ContentUnavailableView("No ideas in this lane", systemImage: "lightbulb",
                                               description: Text("The scan of \(day) found nothing actionable here. Ideas need daily bars for every ticker; the worker pulls them each morning once the data keys are set."))
                    }
                } footer: {
                    Text("Scan of \(day): \(all.lastScan.signals) signals. Every number comes from stored data with its source and time.")
                }
            } else if let all, all.lastScan.day == nil {
                ContentUnavailableView("No scan yet", systemImage: "lightbulb",
                                       description: Text("The worker scans every weekday at 06:20 PT. You can run one now."))
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Ideas")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { investing = true } label: { Label("Invest This", systemImage: "dollarsign.circle") }
            }
            ToolbarItem(placement: .secondaryAction) {
                Button { Task { await scan() } } label: { Label("Scan Now", systemImage: "arrow.triangle.2.circlepath") }
                    .disabled(scanning)
            }
        }
        .sheet(isPresented: $investing) { InvestSheet() }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do {
            all = try await session.client.ideas()
            regime = try? await session.client.marketRegime()
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func scan() async {
        scanning = true
        defer { scanning = false }
        _ = try? await session.client.scanNow()
        await load()
    }
}

struct IdeaCard: View {
    let idea: IdeaRow

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(idea.ticker).font(.headline)
                Text(idea.module.replacingOccurrences(of: "_", with: " ")).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(Show.score(idea.score))").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Text(idea.status.capitalized).font(.caption)
                    .foregroundStyle(idea.isOpen ? Color.green : Color.red)
            }
            Text(idea.reason).font(.callout)
            if idea.isOpen {
                HStack(spacing: 12) {
                    if let q = idea.quantity {
                        Text(idea.lane == "puts" ? "\(Show.quantity(q)) contract\(q == "1" ? "" : "s")" : "\(Show.quantity(q)) shares")
                    }
                    if let e = idea.entry { Text("in \(Show.money(e))") }
                    if let s = idea.stop { Text("stop \(Show.money(s))") }
                    if let c = idea.collateral { Text("collateral \(Show.money(c))") }
                    if let m = idea.maxLoss { Text("risk \(Show.money(m))") }
                }
                .font(.caption).foregroundStyle(.secondary)
            } else if let refusal = idea.refusal {
                Label(refusal, systemImage: "hand.raised").font(.caption).foregroundStyle(.secondary)
            }
            Text("\(idea.source ?? "?") · as of \(Stamp.short(idea.asOf)) · \(idea.horizonDays)-day horizon")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

/// The idea with the signal's evidence, line by line, and the way into practice.
struct IdeaDetailView: View {
    @Environment(SessionModel.self) private var session
    @Environment(PracticeModel.self) private var practice
    let idea: IdeaRow
    @State private var detail: IdeaDetail?

    var body: some View {
        List {
            Section {
                IdeaCard(idea: idea)
            }
            Section("Levels") {
                LabeledContent("Entry", value: Show.money(idea.entry))
                LabeledContent("Stop", value: Show.money(idea.stop))
                LabeledContent("Target", value: Show.money(idea.target))
                if let c = idea.collateral { LabeledContent("Collateral", value: Show.money(c)) }
                LabeledContent("Sized To", value: "\(idea.book) · \(Show.money(idea.balance))")
            }
            if let ev = detail?.signal?.evidence, !ev.lines.isEmpty {
                Section("Evidence") {
                    ForEach(ev.lines, id: \.0) { key, value in
                        LabeledContent(key.replacingOccurrences(of: "_", with: " "), value: value)
                            .font(.callout)
                    }
                }
            }
            Section {
                NavigationLink { ChartsView(ticker: idea.ticker) } label: { Label("Chart", systemImage: "chart.xyaxis.line") }
                NavigationLink { TickerView(ticker: idea.ticker) } label: { Label("News and Earnings", systemImage: "newspaper") }
                Button { practice.openTicket(ticker: idea.ticker) } label: { Label("Try It in Practice", systemImage: "cart") }
            }
        }
        .navigationTitle(idea.ticker)
        .task { detail = try? await session.client.idea(id: idea.id) }
    }
}

extension Show {
    static func score(_ s: String) -> String {
        guard let d = Decimal(string: s) else { return s }
        return d.formatted(.number.precision(.fractionLength(0)))
    }
}
