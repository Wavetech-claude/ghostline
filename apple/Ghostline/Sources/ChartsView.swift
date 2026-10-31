import Charts
import GhostlineKit
import SwiftUI

/// Charts: candlesticks or a line, timeframes from a day to five years, volume, moving averages, RSI, MACD, Bollinger
/// bands, and the support and resistance levels drawn on. Every price shows its source and time.
struct ChartsView: View {
    @Environment(SessionModel.self) private var session
    let ticker: String
    @State private var timeframe = "6M"
    @State private var style = "candles"
    @State private var showBands = false
    @State private var chart: ChartData?
    @State private var error: String?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let timeframes = ["1W", "1M", "3M", "6M", "1Y", "5Y"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Picker("Timeframe", selection: $timeframe) {
                    ForEach(timeframes, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
                if let chart, !chart.bars.isEmpty {
                    PriceChart(chart: chart, style: style, showBands: showBands)
                        .frame(height: 300)
                    VolumeChart(chart: chart).frame(height: 70)
                    RSIChart(chart: chart).frame(height: 90)
                    MACDChart(chart: chart).frame(height: 90)
                    LevelsList(levels: chart.levels)
                    Text("\(chart.source ?? "?") · bars as of \(Stamp.short(chart.asOf)) · daily, delayed data")
                        .font(.caption2).foregroundStyle(.tertiary)
                } else if let error {
                    Text(error).foregroundStyle(.secondary)
                } else if chart != nil {
                    ContentUnavailableView("No bars stored yet", systemImage: "chart.xyaxis.line",
                                           description: Text("The worker pulls daily bars for every holding and watched ticker once its data key is set."))
                } else {
                    ProgressView()
                }
            }
            .padding()
        }
        .navigationTitle(ticker)
        .toolbar {
            ToolbarItem(placement: .secondaryAction) {
                Menu {
                    Picker("Style", selection: $style) {
                        Label("Candles", systemImage: "chart.bar").tag("candles")
                        Label("Line", systemImage: "chart.xyaxis.line").tag("line")
                    }
                    Toggle("Bollinger Bands", isOn: $showBands)
                } label: { Label("Chart Options", systemImage: "slider.horizontal.3") }
            }
        }
        .task(id: timeframe) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do {
            let loaded = try await session.client.chart(ticker: ticker, timeframe: timeframe)
            withAnimation(.ghostline(reduceMotion)) { chart = loaded }
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct PriceChart: View {
    let chart: ChartData
    let style: String
    let showBands: Bool
    @State private var picked: Date?

    /// The bar nearest the finger while it drags across the chart.
    private var pickedBar: BarRow? {
        guard let picked else { return nil }
        return chart.bars.filter { $0.date != nil }.min {
            abs($0.date!.timeIntervalSince(picked)) < abs($1.date!.timeIntervalSince(picked))
        }
    }

    /// The line takes the period's result: gain green when the last close is at or above the first, else loss red.
    private var trend: Color {
        guard let first = chart.bars.first.flatMap({ Double($0.close) }),
              let last = chart.bars.last.flatMap({ Double($0.close) }) else { return .ghost }
        return last >= first ? .gain : .loss
    }

    private var floor: Double { chart.bars.compactMap { Double($0.low) }.min() ?? 0 }

    /// The price axis spans what the window draws (lows, highs, the moving averages, the bands when shown), plus 4 %.
    /// Levels found in older history (a support far below today's prices) used to stretch it, so a 6-month chart of MU
    /// ran from 0 to 1,500 with the prices squeezed into the top third.
    private var span: ClosedRange<Double> {
        var drawn = chart.bars.compactMap { Double($0.low) } + chart.bars.compactMap { Double($0.high) }
        drawn += chart.sma20.compactMap { $0 } + chart.sma50.compactMap { $0 }
        if showBands { drawn += chart.bollinger.upper.compactMap { $0 } + chart.bollinger.lower.compactMap { $0 } }
        guard let lo = drawn.min(), let hi = drawn.max(), hi > lo else { return 0...1 }
        let pad = (hi - lo) * 0.04
        return (lo - pad)...(hi + pad)
    }

    /// The support and resistance levels inside the axis, at most one per 2 % of it, so their labels never overlap.
    private var levels: [Level] {
        var kept: [Level] = []
        let gap = (span.upperBound - span.lowerBound) * 0.02
        for level in chart.levels.sorted(by: { $0.touches > $1.touches }) where span.contains(level.price) {
            if kept.allSatisfy({ abs($0.price - level.price) >= gap }) { kept.append(level) }
        }
        return kept
    }

    var body: some View {
        Chart {
            ForEach(chart.bars) { bar in
                if let d = bar.date, let o = Double(bar.open), let h = Double(bar.high), let l = Double(bar.low),
                   let c = Double(bar.close) {
                    if style == "candles" {
                        RuleMark(x: .value("Date", d), yStart: .value("Low", l), yEnd: .value("High", h))
                            .foregroundStyle(c >= o ? Color.gain : Color.loss)
                            .lineStyle(StrokeStyle(lineWidth: 1))
                        RectangleMark(x: .value("Date", d), yStart: .value("Open", min(o, c)), yEnd: .value("Close", max(o, c)),
                                      width: .ratio(0.6))
                            .foregroundStyle(c >= o ? Color.gain : Color.loss)
                    } else {
                        AreaMark(x: .value("Date", d), yStart: .value("Floor", floor), yEnd: .value("Close", c))
                            .foregroundStyle(LinearGradient(colors: [trend.opacity(0.22), trend.opacity(0)],
                                                            startPoint: .top, endPoint: .bottom))
                            .interpolationMethod(.monotone)
                        LineMark(x: .value("Date", d), y: .value("Close", c))
                            .foregroundStyle(trend)
                            .lineStyle(StrokeStyle(lineWidth: 2))
                            .interpolationMethod(.monotone)
                    }
                }
            }
            ForEach(Array(chart.line(chart.sma20).enumerated()), id: \.offset) { _, p in
                LineMark(x: .value("Date", p.0), y: .value("SMA 20", p.1), series: .value("Line", "SMA 20"))
                    .foregroundStyle(.orange).lineStyle(StrokeStyle(lineWidth: 1))
            }
            ForEach(Array(chart.line(chart.sma50).enumerated()), id: \.offset) { _, p in
                LineMark(x: .value("Date", p.0), y: .value("SMA 50", p.1), series: .value("Line", "SMA 50"))
                    .foregroundStyle(.blue).lineStyle(StrokeStyle(lineWidth: 1))
            }
            if showBands {
                ForEach(Array(chart.line(chart.bollinger.upper).enumerated()), id: \.offset) { _, p in
                    LineMark(x: .value("Date", p.0), y: .value("Upper", p.1), series: .value("Line", "Upper"))
                        .foregroundStyle(.gray.opacity(0.6)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3]))
                }
                ForEach(Array(chart.line(chart.bollinger.lower).enumerated()), id: \.offset) { _, p in
                    LineMark(x: .value("Date", p.0), y: .value("Lower", p.1), series: .value("Line", "Lower"))
                        .foregroundStyle(.gray.opacity(0.6)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3]))
                }
            }
            if let bar = pickedBar, let d = bar.date {
                RuleMark(x: .value("Picked", d))
                    .foregroundStyle(Color.secondary.opacity(0.5))
                    .annotation(position: .top, spacing: 4,
                                overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                        Callout(lines: [Show.money(bar.close),
                                        "O \(Show.money(bar.open))  H \(Show.money(bar.high))  L \(Show.money(bar.low))"],
                                day: d)
                    }
            }
            ForEach(levels) { level in
                RuleMark(y: .value(level.kind, level.price))
                    .foregroundStyle(level.kind == "support" ? Color.teal.opacity(0.7) : Color.purple.opacity(0.7))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [6, 4]))
                    .annotation(position: .trailing, alignment: .leading) {
                        Text(level.price.formatted(.number.precision(.fractionLength(2))))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
            }
        }
        .chartYScale(domain: span)
        .chartLegend(.hidden)
        .chartXSelection(value: $picked)
        .sensoryFeedback(.selection, trigger: pickedBar?.id)
        .accessibilityLabel("Price chart for \(chart.ticker), \(chart.bars.count) daily bars")
    }
}

private struct VolumeChart: View {
    let chart: ChartData

    var body: some View {
        Chart(chart.bars) { bar in
            if let d = bar.date, let v = Double(bar.volume) {
                BarMark(x: .value("Date", d), y: .value("Volume", v))
                    .foregroundStyle(.secondary.opacity(0.5))
            }
        }
        .chartYAxis { AxisMarks(values: .automatic(desiredCount: 2)) }
        .accessibilityLabel("Volume")
    }
}

private struct RSIChart: View {
    let chart: ChartData

    var body: some View {
        Chart {
            ForEach(Array(chart.line(chart.rsi14).enumerated()), id: \.offset) { _, p in
                LineMark(x: .value("Date", p.0), y: .value("RSI", p.1))
                    .foregroundStyle(.indigo)
            }
            RuleMark(y: .value("Overbought", 70)).foregroundStyle(.gray.opacity(0.4)).lineStyle(StrokeStyle(dash: [3]))
            RuleMark(y: .value("Oversold", 30)).foregroundStyle(.gray.opacity(0.4)).lineStyle(StrokeStyle(dash: [3]))
        }
        .chartYScale(domain: 0...100)
        .chartYAxis { AxisMarks(values: [30, 70]) }
        .accessibilityLabel("RSI 14")
    }
}

private struct MACDChart: View {
    let chart: ChartData

    var body: some View {
        Chart {
            ForEach(Array(chart.line(chart.macd.histogram).enumerated()), id: \.offset) { _, p in
                BarMark(x: .value("Date", p.0), y: .value("Histogram", p.1))
                    .foregroundStyle(p.1 >= 0 ? Color.gain.opacity(0.5) : Color.loss.opacity(0.5))
            }
            ForEach(Array(chart.line(chart.macd.macd).enumerated()), id: \.offset) { _, p in
                LineMark(x: .value("Date", p.0), y: .value("MACD", p.1), series: .value("Line", "MACD"))
                    .foregroundStyle(.blue)
            }
            ForEach(Array(chart.line(chart.macd.signal).enumerated()), id: \.offset) { _, p in
                LineMark(x: .value("Date", p.0), y: .value("Signal", p.1), series: .value("Line", "Signal"))
                    .foregroundStyle(.orange)
            }
        }
        .chartLegend(.hidden)
        .accessibilityLabel("MACD")
    }
}

private struct LevelsList: View {
    let levels: [Level]

    var body: some View {
        if !levels.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("Levels from pivot clusters").font(.caption).foregroundStyle(.secondary)
                ForEach(levels) { lv in
                    HStack {
                        Text(lv.kind.capitalized).font(.caption)
                        Spacer()
                        Text("\(lv.price.formatted(.number.precision(.fractionLength(2)))) · \(lv.touches) touches")
                            .font(.caption).monospacedDigit()
                    }
                }
            }
        }
    }
}
