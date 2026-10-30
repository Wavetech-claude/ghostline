import GhostlineKit
import SwiftUI

/// One history for the three books: filters, totals, open positions and closed trades with P&L after fees.
struct HistoryView: View {
    @Environment(LedgerModel.self) private var ledger
    @State private var book = "all"
    @State private var result = "all"

    var body: some View {
        List {
            if let h = ledger.history {
                Section {
                    TotalsRow(totals: h.totals)
                }
                if !h.open.isEmpty {
                    Section("Open") {
                        ForEach(h.open) { p in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text(p.ticker).font(.headline)
                                    Text(p.book ?? "").font(.caption).foregroundStyle(.secondary)
                                    Spacer()
                                    Text(Show.money(p.costBasis)).monospacedDigit()
                                }
                                Text("\(Show.quantity(p.quantity)) at \(Show.money(p.averageCost)) avg")
                                    .font(.caption).foregroundStyle(.secondary)
                                PriceLine(price: p.price, source: p.priceSource, asOf: p.priceAsOf)
                            }
                        }
                    }
                }
                Section("Closed") {
                    ForEach(h.closed) { ClosedRow(trade: $0) }
                    if h.closed.isEmpty { Text("No closed trade yet.").foregroundStyle(.secondary) }
                }
            } else if let error = ledger.error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("History")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { ledger.showingTradeForm = true } label: { Label("Log a Trade", systemImage: "plus") }
                    .keyboardShortcut("n", modifiers: .command)
            }
            ToolbarItem(placement: .secondaryAction) {
                Menu {
                    Picker("Book", selection: $book) {
                        Text("All Books").tag("all")
                        Text("Real").tag("real")
                        Text("Practice").tag("practice")
                        Text("AI Trader").tag("ai")
                    }
                    Picker("Result", selection: $result) {
                        Text("Wins and Losses").tag("all")
                        Text("Wins").tag("win")
                        Text("Losses").tag("loss")
                    }
                } label: { Label("Filter", systemImage: "line.3.horizontal.decrease.circle") }
            }
        }
        .task { await reload() }
        .refreshable { await reload() }
        .onChange(of: book) { Task { await reload() } }
        .onChange(of: result) { Task { await reload() } }
    }

    private func reload() async {
        ledger.filters = TradeFilters(book: book == "all" ? nil : book, result: result == "all" ? nil : result)
        await ledger.loadHistory()
    }
}

struct TotalsRow: View {
    let totals: HistoryTotals

    var body: some View {
        Grid(horizontalSpacing: 16, verticalSpacing: 6) {
            GridRow {
                stat("Trades", "\(totals.trades)")
                stat("Win rate", Show.percent(totals.winRate))
                stat("Net P&L", Show.signedMoney(totals.netPnl))
            }
            GridRow {
                stat("Avg win", Show.money(totals.averageWin))
                stat("Avg loss", Show.money(totals.averageLoss))
                Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
            }
        }
        .padding(.vertical, 4)
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.callout.weight(.medium)).monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

struct ClosedRow: View {
    let trade: ClosedTrade

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(trade.ticker).font(.headline)
                Text([trade.book, trade.asset, trade.lane].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(Show.signedMoney(trade.pnl)).monospacedDigit()
                    .foregroundStyle(Show.isGain(trade.pnl) == true ? .green : .red)
            }
            HStack {
                Text("\(Show.quantity(trade.quantity)) closed \(Stamp.short(trade.at))")
                Spacer()
                if let pct = trade.pnlPct { Text(Show.percent(pct)) }
            }
            .font(.caption).foregroundStyle(.secondary)
            if trade.ideaId != nil || trade.memoId != nil {
                Text("From idea \(trade.ideaId.map(String.init) ?? "—")").font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}
