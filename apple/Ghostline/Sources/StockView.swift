import Charts
import GhostlineKit
import SwiftUI

/// One stock on one page, in the look the owner chose on 2026-10-30 ("Look B, Ghost"):
/// - the big price and its change;
/// - a clean ghost-blue line you drag to read any day;
/// - the time buttons;
/// - then the owner's position, Ghostline's call, the day's numbers, earnings and news, on cards.
/// Candles and indicators wait behind the chart button (ChartsView).
struct TickerView: View {
    @Environment(SessionModel.self) private var session
    @Environment(PracticeModel.self) private var practice
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let ticker: String

    @State private var timeframe = "6M"
    @State private var chart: ChartData?
    @State private var info: NewsAndEarnings?
    @State private var memos: Memos?
    @State private var holdings: [Holding] = []
    @State private var watched = false
    @State private var problem: String?

    var body: some View {
        StockPage(ticker: ticker, timeframe: $timeframe, chart: chart, info: info, memos: memos, holdings: holdings,
                  problem: problem)
            .navigationTitle(ticker)
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await toggleWatch() } } label: {
                        Label(watched ? "Watching" : "Watch", systemImage: watched ? "star.fill" : "star")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    NavigationLink { AlertsView(ticker: ticker) } label: { Label("Price Alerts", systemImage: "bell") }
                }
            }
            .safeAreaBar(edge: .bottom) {
                HStack(spacing: 12) {
                    NavigationLink { DeepDiveView(ticker: ticker) } label: {
                        Text("Ask Ghostline").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.glass)
                    Button { practice.openTicket(ticker: ticker) } label: {
                        Text("Practice Buy").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.glassProminent)
                }
                .controlSize(.large)
                .padding(.horizontal, 16)
                .padding(.bottom, 4)
            }
            .task(id: timeframe) { await loadChart() }
            .task { await loadRest() }
            .refreshable {
                await loadChart()
                await loadRest()
            }
    }

    private func loadChart() async {
        do {
            let loaded = try await session.client.chart(ticker: ticker, timeframe: timeframe)
            withAnimation(.ghostline(reduceMotion)) { chart = loaded }
            problem = nil
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func loadRest() async {
        info = try? await session.client.newsAndEarnings(ticker: ticker)
        memos = try? await session.client.memos(ticker: ticker)
        if let p = try? await session.client.portfolio() {
            holdings = p.books.flatMap { b in
                b.positions.filter { $0.ticker == ticker }.map { Holding(book: b.name, position: $0) }
            }
        }
        if let w = try? await session.client.watchlist() { watched = w.contains { $0.ticker == ticker } }
    }

    private func toggleWatch() async {
        do {
            if watched { try await session.client.unwatch(ticker) } else { try await session.client.watch(ticker) }
            watched.toggle()
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct Holding: Identifiable {
    let book: String
    let position: PortfolioPosition
    var id: String { book }
}

/// A day's close on the line: the date, the close as a number for the chart, and as the exact string for money.
struct StockPoint: Identifiable {
    let day: Date
    let close: Double
    let text: String
    var id: Date { day }
}

/// The page: the sections in a scroll view.
struct StockPage: View {
    let ticker: String
    @Binding var timeframe: String
    let chart: ChartData?
    let info: NewsAndEarnings?
    let memos: Memos?
    let holdings: [Holding]
    let problem: String?

    var body: some View {
        ScrollView {
            StockSections(ticker: ticker, timeframe: $timeframe, chart: chart, info: info, memos: memos,
                          holdings: holdings, problem: problem)
        }
    }
}

/// The page's sections, drawn from what they are given (TickerView loads it). Apart from the scroll view, so a check
/// can draw them: an image renderer draws nothing inside a ScrollView.
struct StockSections: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme
    let ticker: String
    @Binding var timeframe: String
    let chart: ChartData?
    let info: NewsAndEarnings?
    let memos: Memos?
    let holdings: [Holding]
    let problem: String?
    @State private var picked: Date?

    static let timeframes = ["1W", "1M", "3M", "6M", "1Y", "5Y"]

    private var points: [StockPoint] {
        chart?.bars.compactMap { b in
            guard let d = b.date, let c = Double(b.close) else { return nil }
            return StockPoint(day: d, close: c, text: b.close)
        } ?? []
    }

    private var pickedPoint: StockPoint? {
        guard let picked else { return nil }
        return points.min { abs($0.day.timeIntervalSince(picked)) < abs($1.day.timeIntervalSince(picked)) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 0) {
                header.padding(.horizontal, 20).padding(.top, 8)
                if points.isEmpty, chart != nil {
                    ContentUnavailableView("No prices stored for \(ticker) yet", systemImage: "chart.xyaxis.line",
                                           description: Text("The worker pulls daily bars for your holdings and watchlist every morning at 05:15 PT."))
                        .frame(height: 240)
                } else {
                    StockLine(points: points, picked: $picked)
                        .frame(height: 240)
                        .padding(.top, 16)
                }
                timeButtons.padding(.horizontal, 16).padding(.vertical, 14)
            }
            .background(alignment: .top) {
                LinearGradient(colors: [Color.ghost.opacity(scheme == .dark ? 0.35 : 0.16), .clear],
                               startPoint: .top, endPoint: .bottom)
                    .ignoresSafeArea(edges: .top)
            }
            if let problem {
                Text(problem).font(.footnote).foregroundStyle(.loss).padding(.horizontal, 20)
            }
            ForEach(holdings) { h in GhostCard(title: "Your Position · \(h.book)") { PositionNumbers(position: h.position) } }
            GhostCard(title: "Ghostline's Call") { CallSummary(ticker: ticker, memos: memos) }
            if let chart, let bar = chart.bars.last {
                GhostCard(title: "The Day's Numbers") { DayNumbers(chart: chart, bar: bar, timeframe: timeframe) }
            }
            if let info, !info.earnings.isEmpty {
                GhostCard(title: "Earnings") { EarningsLines(earnings: info.earnings) }
            }
            if let info, !info.news.isEmpty {
                GhostCard(title: "News") {
                    ForEach(info.news.prefix(4)) { n in
                        if let url = URL(string: n.url) { Link(destination: url) { NewsLine(item: n) } }
                    }
                }
            }
            GhostCard(title: "More") {
                NavigationLink { OptionsChainView(ticker: ticker) } label: { MoreRow("Options Chain", "list.number") }
                Divider()
                NavigationLink { ChartsView(ticker: ticker) } label: { MoreRow("Advanced Chart", "chart.bar.xaxis") }
            }
        }
        .padding(.bottom, 16)
        .frame(maxWidth: 720, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // ── the price, its change, and the day or the dragged day ──
    private var header: some View {
        let shown = pickedPoint ?? points.last
        let base = pickedPoint != nil ? points.first?.text : (points.count > 1 ? points[points.count - 2].text : nil)
        let change = shown.flatMap { s in base.flatMap { b in Self.difference(s.text, b) } }
        return VStack(alignment: .leading, spacing: 6) {
            Text(ticker).font(.subheadline.weight(.medium)).foregroundStyle(.secondary)
            Text(Show.money(shown?.text)).font(.system(size: 42, weight: .semibold, design: .rounded)).monospacedDigit()
                .contentTransition(.numericText())
                .animation(.ghostline(reduceMotion), value: shown?.text)
            if let change {
                HStack(spacing: 8) {
                    Text("\(Show.signedMoney(change.money))  \(Show.signedPercent(change.percent))").monospacedDigit()
                        .padding(.horizontal, 10).padding(.vertical, 4)
                        .background(Show.tint(change.money).opacity(0.14), in: .capsule)
                        .foregroundStyle(Show.tint(change.money))
                    Text(dayLabel(shown?.day)).foregroundStyle(Color.secondary)
                }
                .font(.subheadline.weight(.semibold))
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func dayLabel(_ day: Date?) -> String {
        guard let day else { return "" }
        if pickedPoint == nil, Calendar.current.isDateInToday(day) { return "Today" }
        return day.formatted(.dateTime.month(.abbreviated).day().year())
    }

    /// The change from `base` to `shown` in money and in percent, in Decimal (money never goes through Double).
    static func difference(_ shown: String, _ base: String) -> (money: String, percent: String)? {
        guard let s = Decimal(string: shown), let b = Decimal(string: base), b != 0 else { return nil }
        let money = s - b, pct = money / b * 100
        var rounded = Decimal(), p = pct
        NSDecimalRound(&rounded, &p, 2, .plain)
        return (NSDecimalNumber(decimal: money).stringValue, NSDecimalNumber(decimal: rounded).stringValue)
    }

    private var timeButtons: some View {
        HStack(spacing: 6) {
            ForEach(Self.timeframes, id: \.self) { t in
                Button { timeframe = t } label: {
                    Text(t).font(.footnote.weight(.semibold))
                        .frame(maxWidth: .infinity).padding(.vertical, 9)
                        // dark text on the light ghost blue: white on it reads 3.1 to 1, black 6.9 to 1
                        .foregroundStyle(t == timeframe ? (scheme == .dark ? Color.black : Color.white) : Color.primary)
                        .background(t == timeframe ? Color.ghost : Color.primary.opacity(0.06), in: .capsule)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(t == timeframe ? .isSelected : [])
            }
            NavigationLink { ChartsView(ticker: ticker) } label: {
                Image(systemName: "chart.bar.xaxis").font(.footnote.weight(.semibold))
                    .padding(9).background(Color.primary.opacity(0.06), in: .circle)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Advanced chart")
        }
    }
}

/// The clean line: ghost blue with a soft glow and fill, a dotted line at the period's first close, no axes. Dragging
/// marks the day under the finger, and the page's price follows it.
struct StockLine: View {
    let points: [StockPoint]
    @Binding var picked: Date?
    var label = "Price line"

    private var pickedPoint: StockPoint? {
        guard let picked else { return nil }
        return points.min { abs($0.day.timeIntervalSince(picked)) < abs($1.day.timeIntervalSince(picked)) }
    }

    private var span: ClosedRange<Double> {
        let closes = points.map(\.close)
        guard let lo = closes.min(), let hi = closes.max(), hi > lo else { return 0...1 }
        return (lo - (hi - lo) * 0.1)...(hi + (hi - lo) * 0.04)
    }

    var body: some View {
        Chart {
            ForEach(points) { p in
                AreaMark(x: .value("Day", p.day), yStart: .value("Floor", span.lowerBound), yEnd: .value("Close", p.close))
                    .foregroundStyle(LinearGradient(colors: [Color.ghost.opacity(0.28), Color.ghost.opacity(0)],
                                                    startPoint: .top, endPoint: .bottom))
                    .interpolationMethod(.monotone)
                LineMark(x: .value("Day", p.day), y: .value("Close", p.close))
                    .foregroundStyle(Color.ghost)
                    .lineStyle(StrokeStyle(lineWidth: 2.2, lineCap: .round))
                    .interpolationMethod(.monotone)
            }
            if let first = points.first {
                RuleMark(y: .value("Start", first.close))
                    .foregroundStyle(Color.secondary.opacity(0.45))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [2, 4]))
            }
            if let p = pickedPoint {
                RuleMark(x: .value("Picked", p.day)).foregroundStyle(Color.secondary.opacity(0.55))
                PointMark(x: .value("Picked", p.day), y: .value("Close", p.close))
                    .symbolSize(110).foregroundStyle(Color.ghost)
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartLegend(.hidden)
        .chartYScale(domain: span)
        .chartXSelection(value: $picked)
        .sensoryFeedback(.selection, trigger: pickedPoint?.day)
        .shadow(color: Color.ghost.opacity(0.45), radius: 8)
        .accessibilityLabel("\(label), \(points.count) days. Drag to read a day.")
    }
}

struct GhostCard<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: .rect(cornerRadius: 18))
        .padding(.horizontal, 16)
    }
}

struct Figure: View {
    let label: String
    let value: String
    var tint: Color = .primary

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.subheadline.weight(.semibold)).monospacedDigit().foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct PositionNumbers: View {
    let position: PortfolioPosition

    var body: some View {
        HStack(alignment: .top) {
            Figure(label: "Shares", value: Show.quantity(position.quantity))
            Figure(label: "Average cost", value: Show.money(position.averageCost))
            Figure(label: "Market value", value: Show.money(position.value ?? position.costBasis))
        }
        HStack(alignment: .top) {
            Figure(label: "Today", value: Show.signedMoney(position.dayPnl), tint: Show.tint(position.dayPnl))
            Figure(label: "Total return", value: Show.signedMoney(position.unrealizedPnl), tint: Show.tint(position.unrealizedPnl))
        }
    }
}

struct CallSummary: View {
    let ticker: String
    let memos: Memos?

    var body: some View {
        if let m = memos?.memos.first {
            HStack(spacing: 10) {
                Text(m.call.uppercased()).font(.caption.weight(.bold))
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(tint(m.call).opacity(0.15), in: .capsule).foregroundStyle(tint(m.call))
                if let lo = m.confidenceLow, let hi = m.confidenceHigh {
                    Text("Confidence \(Show.plainNumber(lo))–\(Show.plainNumber(hi))%").font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                NavigationLink { DeepDiveView(ticker: ticker) } label: { Text("Memo ›").font(.subheadline.weight(.semibold)) }
            }
            if let line = m.base ?? m.bull { Text(line).font(.subheadline).lineLimit(3) }
        } else if memos?.running != nil {
            HStack { ProgressView(); Text("Ghostline is writing a call.").foregroundStyle(.secondary) }
        } else {
            Text("No call yet. Ask Ghostline for one; it takes about a minute.").font(.subheadline).foregroundStyle(.secondary)
        }
    }

    private func tint(_ call: String) -> Color {
        switch call {
        case "buy": .gain
        case "avoid": .loss
        default: .orange
        }
    }
}

struct DayNumbers: View {
    let chart: ChartData
    let bar: BarRow
    let timeframe: String

    var body: some View {
        let highs = chart.bars.compactMap { Decimal(string: $0.high) }, lows = chart.bars.compactMap { Decimal(string: $0.low) }
        HStack(alignment: .top) {
            Figure(label: "Open", value: Show.money(bar.open))
            Figure(label: "High", value: Show.money(bar.high))
            Figure(label: "Low", value: Show.money(bar.low))
        }
        HStack(alignment: .top) {
            // Alpaca's free feed counts one exchange (IEX), so this is far below the stock's whole volume
            Figure(label: "IEX volume", value: (Double(bar.volume) ?? 0).formatted(.number.notation(.compactName)))
            Figure(label: "\(timeframe) high", value: highs.max().map { Money.format($0) } ?? "—")
            Figure(label: "\(timeframe) low", value: lows.min().map { Money.format($0) } ?? "—")
        }
        Text("\(chart.source ?? "?") · \(Stamp.short(chart.asOf)) · daily bars, delayed")
            .font(.caption2).foregroundStyle(.tertiary)
    }
}

struct EarningsLines: View {
    let earnings: [EarningsRow]

    var body: some View {
        let today = Stamp.localDay(Date())
        let next = earnings.filter { $0.at >= today }.min { $0.at < $1.at }
        let last = earnings.filter { $0.at < today }.max { $0.at < $1.at }
        if let next {
            Figure(label: "Next report", value: "\(Stamp.day(next.at)) · estimate \(next.estimate ?? "—")")
        }
        if let last {
            Figure(label: "Last report, \(Stamp.day(last.at))",
                   value: last.movePct.map { "Moved \(Show.signedPercent($0))" } ?? "actual \(last.actual ?? "—")",
                   tint: Show.tint(last.movePct))
        }
    }
}

struct MoreRow: View {
    let title: String
    let symbol: String
    init(_ title: String, _ symbol: String) { self.title = title; self.symbol = symbol }

    var body: some View {
        HStack {
            Label {
                Text(title).foregroundStyle(.primary)
            } icon: {
                Image(systemName: symbol).foregroundStyle(Color.ghost)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .contentShape(.rect)
    }
}
