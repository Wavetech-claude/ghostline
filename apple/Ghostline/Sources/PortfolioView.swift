import Charts
import GhostlineKit
import SwiftUI

/// Portfolio in the Ghost look (owner, 2026-10-30: "like Robinhood, my balance should come with a graph"):
/// - a button per book (Real, Practice, AI, Options);
/// - the book's balance, big, over a ghost-blue line of its value; drag the line to read any day;
/// - time buttons. The change over the period counts gains only: money paid in or out is taken out of it;
/// - cards: today, holdings, the book against the S&P 500, and every book's total.
struct PortfolioView: View {
    @Environment(LedgerModel.self) private var ledger
    @Environment(SessionModel.self) private var session
    @State private var book: String?
    @State private var range = "1M"
    @State private var series: BookSeries?
    @State private var seriesError: String?

    var body: some View {
        ScrollView {
            if let p = ledger.portfolio {
                PortfolioSections(portfolio: p, book: chosen(p), range: $range, series: series, seriesError: seriesError,
                                  select: { series = nil; book = $0 }, addMoney: { ledger.addMoney(to: $0) })
            } else if let error = ledger.error {
                Text(error).foregroundStyle(.secondary).padding()
            } else {
                ProgressView().padding(.top, 80)
            }
        }
        .navigationTitle("Portfolio")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                NavigationLink { HistoryView() } label: { Label("History", systemImage: "list.bullet.rectangle") }
            }
        }
        .task { await ledger.loadPortfolio() }
        .task(id: ledger.portfolio.map { chosen($0) }) { await loadSeries() }
        .refreshable {
            await ledger.loadPortfolio()
            await loadSeries()
        }
    }

    /// The book chosen, else the first one holding money (real first), else real.
    private func chosen(_ p: Portfolio) -> String {
        if let book { return book }
        return ["real", "practice", "ai", "options"].first { k in
            p.books.contains { $0.book == k && (Decimal(string: $0.total) ?? 0) != 0 }
        } ?? "real"
    }

    private func loadSeries() async {
        guard let p = ledger.portfolio else { return }
        do {
            series = try await session.client.series(book: chosen(p))
            seriesError = nil
        } catch {
            series = nil
            seriesError = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

/// The portfolio's sections, drawn from what they are given (PortfolioView loads it). Apart from the scroll view, so
/// a check can draw them.
struct PortfolioSections: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme
    let portfolio: Portfolio
    let book: String
    @Binding var range: String
    let series: BookSeries?
    let seriesError: String?
    let select: (String) -> Void
    let addMoney: (String) -> Void
    @State private var picked: Date?

    static let ranges = ["1W", "1M", "3M", "1Y", "All"]
    private static let days = ["1W": 7, "1M": 31, "3M": 92, "1Y": 366]
    private static let words = ["1W": "past week", "1M": "past month", "3M": "past 3 months", "1Y": "past year",
                                "All": "since the start"]
    private static let short = ["real": "Real", "practice": "Practice", "ai": "AI", "options": "Options"]

    private var current: PortfolioBook? { portfolio.books.first { $0.book == book } }

    /// The series' days inside the chosen range (every day for All).
    private var inRange: [SeriesPoint] {
        guard let all = series?.points else { return [] }
        guard let days = Self.days[range], let last = all.last?.day else { return all }
        let from = last.addingTimeInterval(-Double(days) * 86_400)
        return all.filter { ($0.day ?? .distantPast) >= from }
    }

    private var points: [StockPoint] {
        inRange.compactMap { p in
            guard let d = p.day, let v = Double(p.value) else { return nil }
            return StockPoint(day: d, close: v, text: p.value)
        }
    }

    /// The range up to the dragged day (the whole range when nothing is dragged).
    private var shown: ArraySlice<SeriesPoint> {
        guard let picked, let i = inRange.indices.min(by: {
            abs((inRange[$0].day ?? .distantPast).timeIntervalSince(picked))
                < abs((inRange[$1].day ?? .distantPast).timeIntervalSince(picked))
        }) else { return inRange[...] }
        return inRange[...i]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 0) {
                bookButtons.padding(.horizontal, 16).padding(.top, 8)
                header.padding(.horizontal, 20).padding(.top, 16)
                if points.count > 1 {
                    StockLine(points: points, picked: $picked, label: "Balance line")
                        .frame(height: 220)
                        .padding(.top, 16)
                } else if series != nil, let b = current {
                    VStack(spacing: 12) {
                        Text((Decimal(string: b.total) ?? 0) == 0
                             ? "No money in \(b.name) yet."
                             : "The line starts after its second day.")
                            .foregroundStyle(.secondary)
                        if (Decimal(string: b.total) ?? 0) == 0 {
                            Button("Add Money") { addMoney(b.book) }.buttonStyle(.glassProminent)
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 180)
                } else if let seriesError {
                    Text(seriesError).font(.footnote).foregroundStyle(.secondary).padding(20)
                } else {
                    ProgressView().frame(maxWidth: .infinity, minHeight: 220)
                }
                rangeButtons.padding(.horizontal, 16).padding(.vertical, 14)
            }
            .background(alignment: .top) {
                LinearGradient(colors: [Color.ghost.opacity(scheme == .dark ? 0.35 : 0.16), .clear],
                               startPoint: .top, endPoint: .bottom)
                    .ignoresSafeArea(edges: .top)
            }
            if let b = current {
                GhostCard(title: "Today") {
                    HStack(alignment: .top) {
                        Figure(label: "Day P&L", value: b.dayPnl.map { Show.signedMoney($0) } ?? "Not known yet",
                               tint: Show.tint(b.dayPnl))
                        Figure(label: "Cash", value: Show.money(b.cash))
                        Figure(label: "Invested", value: Show.money(b.invested))
                    }
                    if b.dayPnlPartial == true {
                        Text("Leaves out a position with no close before today's session.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                if !b.positions.isEmpty {
                    GhostCard(title: "Holdings") {
                        ForEach(b.positions) { position in
                            NavigationLink { PositionDetailView(book: b.book, ticker: position.ticker) } label: {
                                HoldingRow(position: position)
                            }
                            .buttonStyle(.plain)
                            if position.id != b.positions.last?.id { Divider() }
                        }
                    }
                }
                if let s = series, s.points.count > 1, let first = s.points.first, let last = s.points.last {
                    GhostCard(title: "Against the S&P 500") {
                        BookReturnChart(name: b.name, points: s.points).frame(height: 160)
                        Text("\(b.name) \(Show.signedPercent(last.twrPct)) since \(Stamp.day(first.date)) · S&P 500 \(Show.signedPercent(last.spyPct)). Both leave out deposits and withdrawals.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            GhostCard(title: "Every Book") {
                ForEach(portfolio.books.filter { $0.book != "ghost" }) { b in
                    HStack {
                        Text(b.name)
                        Spacer()
                        Text(Show.money(b.total)).monospacedDigit()
                    }
                    .font(.subheadline)
                }
                Divider()
                HStack {
                    Text("Total").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(Show.money(portfolio.total)).font(.subheadline.weight(.semibold)).monospacedDigit()
                }
                if let note = portfolio.note { Text(note).font(.caption).foregroundStyle(.secondary) }
            }
        }
        .padding(.bottom, 16)
        .frame(maxWidth: 720, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // ── the balance and its gain over the range, or on the dragged day ──
    private var header: some View {
        let last = shown.last
        let gain = Self.gain(shown)
        let balance = picked == nil ? current?.total : last?.value
        return VStack(alignment: .leading, spacing: 6) {
            Text(current?.name ?? "").font(.subheadline.weight(.medium)).foregroundStyle(.secondary)
            Text(Show.money(balance))
                .font(.system(size: 42, weight: .semibold, design: .rounded)).monospacedDigit()
                .contentTransition(.numericText())
                .animation(.ghostline(reduceMotion), value: balance)
            if let gain {
                HStack(spacing: 8) {
                    Text("\(Show.signedMoney(gain.money))  \(Show.signedPercent(gain.percent))").monospacedDigit()
                        .padding(.horizontal, 10).padding(.vertical, 4)
                        .background(Show.tint(gain.money).opacity(0.14), in: .capsule)
                        .foregroundStyle(Show.tint(gain.money))
                    Text(picked != nil ? (last?.day?.formatted(.dateTime.month(.abbreviated).day().year()) ?? "")
                                       : (Self.words[range] ?? ""))
                        .foregroundStyle(Color.secondary)
                }
                .font(.subheadline.weight(.semibold))
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// The gain from the first day to the last of `days`: the change in value less the money paid in or out after the
    /// first day, and the time-weighted return over the same days. Decimal throughout.
    static func gain(_ days: ArraySlice<SeriesPoint>) -> (money: String, percent: String)? {
        guard days.count > 1, let a = days.first, let b = days.last,
              let va = Decimal(string: a.value), let vb = Decimal(string: b.value),
              let ta = Decimal(string: a.twrPct), let tb = Decimal(string: b.twrPct), 1 + ta / 100 != 0 else { return nil }
        let flows = days.dropFirst().reduce(Decimal(0)) { $0 + (Decimal(string: $1.netFlow) ?? 0) }
        var pct = ((1 + tb / 100) / (1 + ta / 100) - 1) * 100, rounded = Decimal()
        NSDecimalRound(&rounded, &pct, 2, .plain)
        return (NSDecimalNumber(decimal: vb - va - flows).stringValue, NSDecimalNumber(decimal: rounded).stringValue)
    }

    private var bookButtons: some View {
        HStack(spacing: 6) {
            ForEach(portfolio.books.filter { $0.book != "ghost" }) { b in
                Button { select(b.book) } label: {
                    Text(Self.short[b.book] ?? b.name).font(.footnote.weight(.semibold))
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .foregroundStyle(b.book == book ? (scheme == .dark ? Color.black : Color.white) : Color.primary)
                        .background(b.book == book ? Color.ghost : Color.primary.opacity(0.06), in: .capsule)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(b.name)
                .accessibilityAddTraits(b.book == book ? .isSelected : [])
            }
        }
    }

    private var rangeButtons: some View {
        HStack(spacing: 6) {
            ForEach(Self.ranges, id: \.self) { r in
                Button { range = r } label: {
                    Text(r).font(.footnote.weight(.semibold))
                        .frame(maxWidth: .infinity).padding(.vertical, 9)
                        .foregroundStyle(r == range ? (scheme == .dark ? Color.black : Color.white) : Color.primary)
                        .background(r == range ? Color.ghost : Color.primary.opacity(0.06), in: .capsule)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(r == range ? .isSelected : [])
            }
        }
    }
}

/// A holding: ticker and shares, then its value and today's change in a pill.
struct HoldingRow: View {
    let position: PortfolioPosition

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(position.ticker).font(.headline)
                Text("\(Show.quantity(position.quantity)) shares · avg \(Show.money(position.averageCost))")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Text(Show.money(position.value ?? position.costBasis)).font(.subheadline.weight(.semibold)).monospacedDigit()
                if let day = position.dayPnl {
                    Text("\(Show.signedMoney(day)) today").font(.caption.weight(.semibold)).monospacedDigit()
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .background(Show.tint(day).opacity(0.14), in: .capsule)
                        .foregroundStyle(Show.tint(day))
                }
            }
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }
}

struct BookReturnChart: View {
    let name: String
    let points: [SeriesPoint]
    @State private var picked: Date?

    /// The point nearest the finger while it drags across the chart.
    private var pickedPoint: SeriesPoint? {
        guard let picked else { return nil }
        return points.filter { $0.day != nil }.min {
            abs($0.day!.timeIntervalSince(picked)) < abs($1.day!.timeIntervalSince(picked))
        }
    }

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
            if let p = pickedPoint, let d = p.day {
                RuleMark(x: .value("Picked", d))
                    .foregroundStyle(Color.secondary.opacity(0.5))
                    .annotation(position: .top, spacing: 4,
                                overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                        Callout(lines: ["\(name) \(Show.signedPercent(p.twrPct))",
                                        "S&P 500 \(Show.signedPercent(p.spyPct))"], day: d)
                    }
            }
        }
        .chartXSelection(value: $picked)
        .sensoryFeedback(.selection, trigger: pickedPoint?.id)
        // green and red mean gain and loss in this app, so the benchmark is a neutral, dashed line
        .chartForegroundStyleScale(domain: [name, "S&P 500"], range: [Color.ghost, Color.gray])
        .chartYAxisLabel("time-weighted return, %")
        .accessibilityLabel("Time-weighted return of \(name) against the S&P 500")
    }
}

/// What a chart says at the point under the finger: the values, then the day.
struct Callout: View {
    let lines: [String]
    let day: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(lines, id: \.self) { Text($0).font(.caption.weight(.semibold)).monospacedDigit() }
            Text(day, format: .dateTime.month(.abbreviated).day().year()).font(.caption2).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .background(.background.secondary, in: .rect(cornerRadius: 8))
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
