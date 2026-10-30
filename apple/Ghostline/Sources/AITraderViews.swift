import Charts
import GhostlineKit
import SwiftUI

/// The AI trader against its S&P 500 ghost: the same deposits on the same dates, compared by time-weighted return so
/// deposits never look like gains. Every trade with the idea and the reason behind it, and Copy.
struct AITraderView: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger
    @State private var answer: AITraderAnswer?
    @State private var error: String?
    @State private var running = false
    @State private var copied: String?

    var body: some View {
        List {
            if let a = answer {
                Section {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("AI Trader").font(.caption).foregroundStyle(.secondary)
                            Text(Show.money(a.state.equity)).font(.system(.title, design: .rounded, weight: .semibold)).monospacedDigit()
                            Text(twr(a.twrPct.ai)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text("S&P 500 Ghost").font(.caption).foregroundStyle(.secondary)
                            Text(Show.money(a.series.ghost.last?.value)).font(.system(.title3, design: .rounded)).monospacedDigit()
                            Text(twr(a.twrPct.ghost)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityElement(children: .combine)
                    if a.series.ai.count > 1 {
                        ReturnChart(ai: a.series.ai, ghost: a.series.ghost).frame(height: 200)
                    } else {
                        Text("The chart starts once the book has been funded for two trading days.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    LabeledContent("Cash", value: Show.money(a.state.cash))
                    LabeledContent("Available", value: Show.money(a.state.available))
                    LabeledContent("Positions", value: "\(a.state.openPositions)")
                }
                Section("Lanes") {
                    ForEach(a.lanes) { lane in
                        LabeledContent(lane.lane.capitalized,
                                       value: lane.graduated ? "Graduated" : "Practice only (\(lane.settledN) settled)")
                    }
                }
                Section("Trades") {
                    ForEach(a.trades) { t in TraderTradeRow(trade: t, copy: { to in Task { await copy(t, to) } }) }
                    if a.trades.isEmpty {
                        Text("No trades yet. Fund the book; it trades the app's open ideas each weekday after the 06:20 scan.")
                            .foregroundStyle(.secondary)
                    }
                    if let copied { Text(copied).font(.footnote).foregroundStyle(.secondary) }
                }
                if !a.ghostTrades.isEmpty {
                    Section("The Ghost's Trades") {
                        ForEach(a.ghostTrades) { t in
                            Text("\(Stamp.day(t.at)) · \(t.side) \(Show.quantity(t.quantity)) SPY at \(Show.money(t.fill))")
                                .font(.callout)
                        }
                    }
                }
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("AI Trader")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { ledger.addMoney(to: "ai") } label: { Label("Add Money", systemImage: "plus") }
            }
            ToolbarItem(placement: .secondaryAction) {
                Button { Task { await run() } } label: { Label("Run Now", systemImage: "play") }.disabled(running)
            }
        }
        .task { await load() }
        .refreshable { await load() }
        .onChange(of: ledger.showingCashFlow) { if !ledger.showingCashFlow { Task { await load() } } }
    }

    private func twr(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "no return yet" }
        return "\(d >= 0 ? "+" : "")\(d.formatted(.number.precision(.fractionLength(2))))% time-weighted"
    }

    private func load() async {
        do {
            answer = try await session.client.aiTrader()
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func run() async {
        running = true
        defer { running = false }
        _ = try? await session.client.runAITrader()
        await load()
    }

    private func copy(_ t: TraderTrade, _ to: String) async {
        do {
            _ = try await session.client.copyTrade(id: t.id, to: to)
            copied = to == "practice" ? "Copied \(t.ticker) into practice." : "\(t.ticker) is on your to-do list."
        } catch {
            copied = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct ReturnChart: View {
    let ai: [SeriesPoint]
    let ghost: [SeriesPoint]

    var body: some View {
        Chart {
            ForEach(ai) { p in
                if let d = p.day, let v = Double(p.twrPct) {
                    LineMark(x: .value("Date", d), y: .value("Return %", v), series: .value("Book", "AI trader"))
                        .foregroundStyle(by: .value("Book", "AI trader"))
                }
            }
            ForEach(ghost) { p in
                if let d = p.day, let v = Double(p.twrPct) {
                    LineMark(x: .value("Date", d), y: .value("Return %", v), series: .value("Book", "S&P 500 ghost"))
                        .foregroundStyle(by: .value("Book", "S&P 500 ghost"))
                        .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                }
            }
            RuleMark(y: .value("Zero", 0)).foregroundStyle(.gray.opacity(0.3))
        }
        // green and red mean gain and loss in this app, so the benchmark is a neutral, dashed line
        .chartForegroundStyleScale(domain: ["AI trader", "S&P 500 ghost"], range: [Color.accentColor, Color.gray])
        .chartYAxisLabel("time-weighted return, %")
        .accessibilityLabel("Time-weighted return of the AI trader against the S&P 500 ghost")
    }
}

struct TraderTradeRow: View {
    let trade: TraderTrade
    var copy: ((String) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("\(trade.side.replacingOccurrences(of: "_", with: " ").capitalized) \(Show.quantity(trade.quantity)) \(trade.optionSymbol ?? trade.ticker)")
                    .font(.headline)
                Spacer()
                Text("at \(Show.money(trade.fill))").monospacedDigit().font(.callout)
            }
            if let note = trade.note { Text(note).font(.caption).foregroundStyle(.secondary).lineLimit(4) }
            HStack {
                Text(Stamp.short(trade.at)).font(.caption2).foregroundStyle(.tertiary)
                Spacer()
                if let copy {
                    Menu {
                        Button("Copy into Practice") { copy("practice") }
                        Button("Copy for Real Money") { copy("real") }.disabled(!trade.copyRealAllowed)
                        if let why = trade.copyRealReason { Text(why) }
                    } label: { Label("Copy", systemImage: "doc.on.doc") }
                    .menuStyle(.button)
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
        }
        .padding(.vertical, 2)
    }
}

/// Options practice on any amount: the app sells the put ideas the amount can cover, on simulated money, with real
/// chains; assignment and expiry are simulated.
struct OptionsPracticeView: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger
    @State private var answer: OptionsPracticeAnswer?
    @State private var error: String?
    @State private var running = false

    var body: some View {
        List {
            if let a = answer {
                Section {
                    LabeledContent("Value", value: Show.money(a.state.equity))
                    LabeledContent("Cash", value: Show.money(a.state.cash))
                    LabeledContent("Held as Collateral", value: Show.money(a.state.lockedCollateral))
                    LabeledContent("Available", value: Show.money(a.state.available))
                    LabeledContent("Puts Sold", value: "\(a.state.shortPuts)")
                    if let t = a.twrPct { LabeledContent("Return, Time-Weighted", value: "\(t)%") }
                } footer: {
                    Text("Add any amount. Each weekday after the scan it sells the put ideas the amount can cover, one contract each.")
                }
                Section("Trades") {
                    ForEach(a.trades) { TraderTradeRow(trade: $0) }
                    if a.trades.isEmpty { Text("No trades yet.").foregroundStyle(.secondary) }
                }
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Options Practice")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { ledger.addMoney(to: "options") } label: { Label("Add Money", systemImage: "plus") }
            }
            ToolbarItem(placement: .secondaryAction) {
                Button { Task { await run() } } label: { Label("Run Now", systemImage: "play") }.disabled(running)
            }
        }
        .task { await load() }
        .refreshable { await load() }
        .onChange(of: ledger.showingCashFlow) { if !ledger.showingCashFlow { Task { await load() } } }
    }

    private func load() async {
        do {
            answer = try await session.client.optionsPractice()
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func run() async {
        running = true
        defer { running = false }
        _ = try? await session.client.runOptionsPractice()
        await load()
    }
}
