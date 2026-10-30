import Charts
import GhostlineKit
import SwiftUI

/// Portfolio: total value, each book's value over time against SPY, its cash, day and total P&L and positions,
/// every price with its source and time. A position opens its own screen.
struct PortfolioView: View {
    @Environment(LedgerModel.self) private var ledger

    var body: some View {
        List {
            if let p = ledger.portfolio {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Total").font(.caption).foregroundStyle(.secondary)
                        Text(Show.money(p.total)).font(.system(.largeTitle, design: .rounded, weight: .semibold)).monospacedDigit()
                        if let note = p.note { Text(note).font(.footnote).foregroundStyle(.secondary) }
                    }
                    .padding(.vertical, 4)
                }
                BookReturnSection(books: p.books)
                ForEach(p.books) { book in
                    Section(book.name) {
                        LabeledContent("Cash", value: Show.money(book.cash))
                        LabeledContent("Invested", value: Show.money(book.invested))
                        LabeledContent("Total", value: Show.money(book.total))
                        if let pnl = book.totalPnl {
                            LabeledContent("Total P&L") {
                                Text(Show.signedMoney(pnl)).foregroundStyle(tint(pnl))
                            }
                        }
                        if book.dayPnl != nil || book.dayPnlPartial == true {
                            PnLRow(title: "Day P&L", value: book.dayPnl, missing: "Not known yet")
                            if book.dayPnlPartial == true {
                                Text("Leaves out a position with no close before today's session.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        ForEach(book.positions) { position in
                            NavigationLink { PositionDetailView(book: book.book, ticker: position.ticker) } label: {
                                PositionRow(position: position)
                            }
                        }
                        if !book.allocation.isEmpty {
                            HStack(spacing: 8) {
                                ForEach(book.allocation) { a in
                                    Text("\(a.name) \(Show.percentOfOne(a.share))")
                                        .font(.caption)
                                        .padding(.horizontal, 8).padding(.vertical, 4)
                                        .background(.fill.tertiary, in: .capsule)
                                }
                            }
                        }
                    }
                }
            } else if let error = ledger.error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Portfolio")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                NavigationLink { HistoryView() } label: { Label("History", systemImage: "list.bullet.rectangle") }
            }
        }
        .task { await ledger.loadPortfolio() }
        .refreshable { await ledger.loadPortfolio() }
    }

    private func tint(_ s: String?) -> Color {
        switch Show.isGain(s) {
        case .some(true): .green
        case .some(false): .red
        case .none: .primary
        }
    }
}

struct PositionRow: View {
    let position: PortfolioPosition

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(position.ticker).font(.headline)
                if let lane = position.lane { Text(lane).font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Text(Show.money(position.value ?? position.costBasis)).monospacedDigit()
            }
            HStack {
                Text("\(Show.quantity(position.quantity)) at \(Show.money(position.averageCost)) avg")
                Spacer()
                if let day = position.dayPnl {
                    Text("\(Show.signedMoney(day)) today").foregroundStyle(PnLRow.tint(day))
                }
                if let pnl = position.unrealizedPnl {
                    Text("\(Show.signedMoney(pnl)) open").foregroundStyle(PnLRow.tint(pnl))
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            PriceLine(price: position.price, source: position.priceSource, asOf: position.priceAsOf)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

/// One book's time-weighted return against SPY's return over the same days. Both ignore deposits and withdrawals,
/// so the two lines compare like with like.
struct BookReturnSection: View {
    @Environment(SessionModel.self) private var session
    let books: [PortfolioBook]
    @State private var book = "real"
    @State private var series: BookSeries?
    @State private var error: String?

    private var name: String { books.first { $0.book == book }?.name ?? book }

    var body: some View {
        Section("Value Over Time") {
            Picker("Book", selection: $book) {
                ForEach(books) { Text($0.name).tag($0.book) }
            }
            if let series, let first = series.points.first, let last = series.points.last {
                BookReturnChart(name: name, points: series.points)
                    .frame(height: 180)
                Text("\(name) \(Show.percent(last.twrPct)) since \(Stamp.day(first.date)) · S&P 500 \(Show.percent(last.spyPct))")
                    .font(.caption).foregroundStyle(.secondary)
            } else if series != nil {
                Text("No money in this book yet.").foregroundStyle(.secondary)
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .task(id: book) { await load() }
    }

    private func load() async {
        do {
            series = try await session.client.series(book: book)
            error = nil
        } catch {
            series = nil
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct BookReturnChart: View {
    let name: String
    let points: [SeriesPoint]

    var body: some View {
        Chart {
            ForEach(points) { p in
                if let d = p.day, let v = Double(p.twrPct) {
                    LineMark(x: .value("Date", d), y: .value("Return %", v), series: .value("Line", name))
                        .foregroundStyle(by: .value("Line", name))
                }
            }
            ForEach(points) { p in
                if let d = p.day, let s = p.spyPct, let v = Double(s) {
                    LineMark(x: .value("Date", d), y: .value("Return %", v), series: .value("Line", "S&P 500"))
                        .foregroundStyle(by: .value("Line", "S&P 500"))
                        .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                }
            }
            RuleMark(y: .value("Zero", 0)).foregroundStyle(.gray.opacity(0.3))
        }
        // green and red mean gain and loss in this app, so the benchmark is a neutral, dashed line
        .chartForegroundStyleScale(domain: [name, "S&P 500"], range: [Color.accentColor, Color.gray])
        .chartYAxisLabel("time-weighted return, %")
        .accessibilityLabel("Time-weighted return of \(name) against the S&P 500")
    }
}

/// Every price shows its source and time; a missing price says so instead of showing a number.
struct PriceLine: View {
    let price: String?
    let source: String?
    let asOf: String?

    var body: some View {
        if let price {
            Text("\(Show.money(price)) · \(source ?? "?") · \(Stamp.short(asOf))")
                .font(.caption2).foregroundStyle(.tertiary)
        } else {
            Text("No stored price yet; shown at cost").font(.caption2).foregroundStyle(.tertiary)
        }
    }
}

extension Show {
    static func percentOfOne(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "—" }
        return d.formatted(.percent.precision(.fractionLength(0...1)))
    }
}
