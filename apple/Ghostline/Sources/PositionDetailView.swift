import GhostlineKit
import SwiftUI

/// One stock in one book: what is held and at what cost, today's and total P&L, the thesis it was bought on,
/// Ghostline's latest call, recent news and the next report. Every price shows its source and time.
struct PositionDetailView: View {
    @Environment(SessionModel.self) private var session
    let book: String
    let ticker: String
    @State private var detail: PositionDetail?
    @State private var error: String?

    var body: some View {
        List {
            if let d = detail {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(Show.money(d.value))
                            .font(.system(.largeTitle, design: .rounded, weight: .semibold)).monospacedDigit()
                        if let average = d.averageCost {
                            Text("\(Show.quantity(d.quantity)) shares at \(Show.money(average)) average")
                                .font(.callout).foregroundStyle(.secondary)
                        } else {
                            Text("No shares held now").font(.callout).foregroundStyle(.secondary)
                        }
                        PriceLine(price: d.price, source: d.priceSource, asOf: d.priceAsOf)
                    }
                    .padding(.vertical, 4)
                    .accessibilityElement(children: .combine)
                }
                Section("Profit and Loss") {
                    PnLRow(title: "Today", value: d.dayPnl, missing: "No close before this session yet")
                    PnLRow(title: "Open", value: d.unrealizedPnl, missing: "No stored price yet")
                    PnLRow(title: "Closed", value: d.realizedPnl, missing: "—")
                    PnLRow(title: "Total", value: d.totalPnl, missing: "No stored price yet")
                }
                if let thesis = d.thesis {
                    Section("Thesis") {
                        Text(thesis.reason)
                        LabeledContent("Stop", value: Show.money(thesis.stop))
                        LabeledContent("Target", value: Show.money(thesis.target))
                        LabeledContent("Idea", value: "\(thesis.lane) · \(thesis.module) · \(thesis.status)")
                        NavigationLink { IdeaLoaderView(id: thesis.ideaId) } label: {
                            Label("Open the Idea", systemImage: "lightbulb")
                        }
                    }
                }
                Section("Ghostline's Call") {
                    if let memo = d.memo {
                        MemoCard(memo: memo)
                    } else {
                        Text("No deep dive yet.").foregroundStyle(.secondary)
                    }
                    NavigationLink { DeepDiveView(ticker: ticker) } label: {
                        Label(d.memo == nil ? "Ask for a Deep Dive" : "All Deep Dives", systemImage: "sparkles")
                    }
                }
                Section("Next Earnings") {
                    if let report = d.nextEarnings {
                        LabeledContent(Stamp.day(report.at), value: "estimate \(report.estimate ?? "—")")
                        Text("\(report.source) · as of \(Stamp.short(report.asOf))")
                            .font(.caption2).foregroundStyle(.tertiary)
                    } else {
                        Text("No report date stored.").foregroundStyle(.secondary)
                    }
                }
                if !d.news.isEmpty {
                    Section("News") {
                        ForEach(d.news) { item in
                            if let url = URL(string: item.url) {
                                Link(destination: url) { NewsLine(item: item) }
                            }
                        }
                    }
                }
                Section {
                    NavigationLink { ChartsView(ticker: ticker) } label: {
                        Label("Chart", systemImage: "chart.xyaxis.line")
                    }
                    NavigationLink { TickerView(ticker: ticker) } label: {
                        Label("News, Earnings and Filings", systemImage: "newspaper")
                    }
                }
            } else if let error {
                ContentUnavailableView(error, systemImage: "exclamationmark.triangle")
            } else {
                ProgressView()
            }
        }
        .navigationTitle(ticker)
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do {
            detail = try await session.client.position(book: book, ticker: ticker)
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

/// A profit or loss with its sign, so gain and loss never depend on color alone.
struct PnLRow: View {
    let title: String
    let value: String?
    let missing: String

    var body: some View {
        LabeledContent(title) {
            if let value {
                Text(Show.signedMoney(value)).monospacedDigit().foregroundStyle(Show.tint(value))
            } else {
                Text(missing).foregroundStyle(.secondary)
            }
        }
    }
}

struct NewsLine: View {
    let item: NewsRow

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(item.headline).font(.callout).foregroundStyle(.primary)
            Text("\(item.source) · \(Stamp.short(item.publishedAt))").font(.caption2).foregroundStyle(.tertiary)
        }
    }
}

/// Opens an idea by its id: a trade or a position remembers only the id.
struct IdeaLoaderView: View {
    @Environment(SessionModel.self) private var session
    let id: Int
    @State private var idea: IdeaRow?
    @State private var error: String?

    var body: some View {
        Group {
            if let idea {
                IdeaDetailView(idea: idea)
            } else if let error {
                ContentUnavailableView(error, systemImage: "lightbulb.slash")
            } else {
                ProgressView()
            }
        }
        .task {
            do {
                idea = try await session.client.ideaRow(id: id)
            } catch {
                self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
            }
        }
    }
}

/// Why a closed trade happened: the idea it came from and the memo behind it.
struct TradeReasonsView: View {
    @Environment(SessionModel.self) private var session
    let trade: ClosedTrade
    @State private var idea: IdeaRow?
    @State private var memo: Memo?
    @State private var error: String?

    var body: some View {
        List {
            Section { ClosedRow(trade: trade) }
            if trade.ideaId != nil {
                Section("Idea") {
                    if let idea {
                        NavigationLink { IdeaDetailView(idea: idea) } label: { IdeaCard(idea: idea) }
                    } else if error == nil {
                        ProgressView()
                    }
                }
            }
            if trade.memoId != nil {
                Section("Memo") {
                    if let memo { MemoCard(memo: memo) } else if error == nil { ProgressView() }
                }
            }
            if let error { Text(error).foregroundStyle(.secondary) }
        }
        .navigationTitle("Why \(trade.ticker)")
        .task { await load() }
    }

    private func load() async {
        do {
            if let id = trade.ideaId { idea = try await session.client.ideaRow(id: id) }
            if let id = trade.memoId { memo = try await session.client.memo(id: id) }
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}
