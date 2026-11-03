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
    @State private var analysts: AnalystViews?
    @State private var memos: Memos?
    @State private var holdings: [Holding] = []
    @State private var watched = false
    @State private var problem: String?

    var body: some View {
        StockPage(ticker: ticker, timeframe: $timeframe, chart: chart, info: info, analysts: analysts, memos: memos,
                  holdings: holdings, problem: problem)
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

    /// The page's other parts, asked for at the same time (they were asked one after another), and set together so
    /// the page draws once for them.
    private func loadRest() async {
        let client = session.client, t = ticker
        async let news = try? client.newsAndEarnings(ticker: t)
        async let views = try? client.analysts(ticker: t)
        async let calls = try? client.memos(ticker: t)
        async let book = try? client.portfolio()
        async let list = try? client.watchlist()
        let (n, a, m, p, w) = await (news, views, calls, book, list)
        info = n
        analysts = a
        memos = m
        if let p {
            holdings = p.books.flatMap { b in
                b.positions.filter { $0.ticker == t }.map { Holding(book: b.name, position: $0) }
            }
        }
        if let w { watched = w.contains { $0.ticker == t } }
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
    let day: Date               // where it sits on the x axis
    let close: Double
    let text: String
    var real: Date? = nil       // an intraday point: the bar's own moment (the x axis has the nights taken out)
    var id: Date { day }
}

extension ChartData {
    /// The line's points, one per bar that has a place on the x axis and a close (the stock page and the preview).
    /// No date is read here: the moments and the axis places were worked out when the chart arrived.
    var stockPoints: [StockPoint] {
        let intraday = isIntraday
        return bars.indices.compactMap { i in
            guard let x = plotDates[i], let c = Double(bars[i].close) else { return nil }
            return StockPoint(day: x, close: c, text: bars[i].close, real: intraday ? instants[i] : nil)
        }
    }
}

/// The page: the sections in a scroll view.
struct StockPage: View {
    let ticker: String
    @Binding var timeframe: String
    let chart: ChartData?
    let info: NewsAndEarnings?
    var analysts: AnalystViews? = nil
    let memos: Memos?
    let holdings: [Holding]
    let problem: String?

    var body: some View {
        ScrollView {
            StockSections(ticker: ticker, timeframe: $timeframe, chart: chart, info: info, analysts: analysts,
                          memos: memos, holdings: holdings, problem: problem)
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
    var analysts: AnalystViews? = nil
    let memos: Memos?
    let holdings: [Holding]
    let problem: String?
    @State private var picked: Date?

    static let timeframes = ["1D", "1W", "1M", "3M", "6M", "1Y", "5Y"]

    private var points: [StockPoint] { chart?.stockPoints ?? [] }

    /// What the change is measured from. A 1D chart: the previous session's close, as Robinhood does. Another
    /// intraday chart, or a dragged point: the first point. A daily chart: the day before the last.
    private var baseText: String? {
        if chart?.timeframe == "1D", let prev = chart?.previousClose { return prev }
        if pickedPoint != nil || chart?.isIntraday == true { return points.first?.text }
        return points.count > 1 ? points[points.count - 2].text : nil
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
                    StockLine(points: points, picked: $picked,
                              baseline: chart?.timeframe == "1D" ? chart?.previousClose.flatMap(Double.init) : nil)
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
            if let analysts, !analysts.isEmpty {
                GhostCard(title: "Analysts") { AnalystLines(views: analysts, price: chart?.bars.last?.close) }
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
        let change = shown.flatMap { s in baseText.flatMap { b in Self.difference(s.text, b) } }
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
                    Text(shown?.real.map(timeLabel) ?? dayLabel(shown?.day)).foregroundStyle(Color.secondary)
                }
                .font(.subheadline.weight(.semibold))
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("stockHeader")
    }

    /// An intraday point: its time, with the weekday on a 1W chart; "Today" for the live end of a 1D chart.
    private func timeLabel(_ moment: Date) -> String {
        if pickedPoint == nil { return chart?.marketOpen == true ? "Today" : "At the close" }
        if chart?.timeframe == "1D" { return moment.formatted(date: .omitted, time: .shortened) }
        return moment.formatted(.dateTime.weekday(.abbreviated).hour().minute())
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
    var baseline: Double? = nil         // the dotted line; the first point when none is given

    private var pickedPoint: StockPoint? {
        guard let picked else { return nil }
        return points.min { abs($0.day.timeIntervalSince(picked)) < abs($1.day.timeIntervalSince(picked)) }
    }

    private var span: ClosedRange<Double> {
        let closes = points.map(\.close) + (baseline.map { [$0] } ?? [])
        guard let lo = closes.min(), let hi = closes.max(), hi > lo else { return 0...1 }
        return (lo - (hi - lo) * 0.1)...(hi + (hi - lo) * 0.04)
    }

    /// A point of the fill: from the floor of the scale up to the close.
    private struct Fill: Identifiable {
        let day: Date
        let floor: Double
        let close: Double
        var id: Date { day }
    }

    var body: some View {
        let scale = span, floor = scale.lowerBound
        Chart {
            // Plots, not one mark per point: a 5-year line (1,250 points) drew in 494 ms as marks and 28 ms as plots
            // (measured 2026-11-03), and dragging redraws it on every move of the finger.
            AreaPlot(points.map { Fill(day: $0.day, floor: floor, close: $0.close) }, x: .value("Day", \.day),
                     yStart: .value("Floor", \.floor), yEnd: .value("Close", \.close))
                .foregroundStyle(LinearGradient(colors: [Color.ghost.opacity(0.28), Color.ghost.opacity(0)],
                                                startPoint: .top, endPoint: .bottom))
                .interpolationMethod(.monotone)
            LinePlot(points, x: .value("Day", \.day), y: .value("Close", \.close))
                .foregroundStyle(Color.ghost)
                .lineStyle(StrokeStyle(lineWidth: 2.2, lineCap: .round))
                .interpolationMethod(.monotone)
            if let start = baseline ?? points.first?.close {
                RuleMark(y: .value("Start", start))
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
        .chartYScale(domain: scale)
        .chartXSelection(value: $picked)
        .sensoryFeedback(.selection, trigger: pickedPoint?.day)
        .shadow(color: Color.ghost.opacity(0.45), radius: 8)
        .accessibilityLabel(points.first?.real != nil ? "\(label), \(points.count) intraday bars. Drag to read a time."
                                                      : "\(label), \(points.count) days. Drag to read a day.")
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

    /// The latest session's numbers. A daily chart: its last bar. An intraday chart: the session's bars added up
    /// (the open of the first, the highest high, the lowest low, the volume summed).
    private var day: (open: String, high: String, low: String, volume: Double) {
        guard chart.isIntraday, let last = chart.instants.last ?? nil else {
            return (bar.open, bar.high, bar.low, Double(bar.volume) ?? 0)
        }
        let lastDay = Stamp.newYorkDay(last)
        let session = zip(chart.bars, chart.instants).filter { $0.1.map(Stamp.newYorkDay) == lastDay }.map(\.0)
        let highs = session.compactMap { Decimal(string: $0.high) }, lows = session.compactMap { Decimal(string: $0.low) }
        return (session.first?.open ?? bar.open, highs.max().map { "\($0)" } ?? bar.high,
                lows.min().map { "\($0)" } ?? bar.low, session.compactMap { Double($0.volume) }.reduce(0, +))
    }

    var body: some View {
        let highs = chart.bars.compactMap { Decimal(string: $0.high) }, lows = chart.bars.compactMap { Decimal(string: $0.low) }
        let d = day
        HStack(alignment: .top) {
            Figure(label: "Open", value: Show.money(d.open))
            Figure(label: "High", value: Show.money(d.high))
            Figure(label: "Low", value: Show.money(d.low))
        }
        HStack(alignment: .top) {
            // Alpaca's free feed counts one exchange (IEX), so this is far below the stock's whole volume
            Figure(label: "IEX volume", value: d.volume.formatted(.number.notation(.compactName)))
            Figure(label: "\(timeframe) high", value: highs.max().map { Money.format($0) } ?? "—")
            Figure(label: "\(timeframe) low", value: lows.min().map { Money.format($0) } ?? "—")
        }
        Text(chart.sourceLine)
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
            let when = ReportHour.words(next.hour).map { " · \($0.lowercased())" } ?? ""
            Figure(label: "Next report", value: "\(Stamp.day(next.at))\(when) · estimate \(next.estimate ?? "—")")
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
