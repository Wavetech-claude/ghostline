import GhostlineKit
import SwiftUI

/// The earnings calendar: reports of the stocks the owner holds, watches or has an open idea on, grouped by day. A
/// past report shows the actual against the estimate and how the stock moved; a coming one shows the estimate and how
/// far the stock usually moved around its last four reports. Every row opens the stock page.
struct EarningsCalendarView: View {
    @Environment(SessionModel.self) private var session
    @State private var calendar: EarningsCalendar?
    @State private var days = 30
    @State private var problem: String?

    var body: some View {
        List {
            Section {
                Picker("Look ahead", selection: $days) {
                    Text("2 weeks").tag(14)
                    Text("30 days").tag(30)
                    Text("90 days").tag(90)
                }
                .pickerStyle(.segmented)
            } footer: {
                Text("Your holdings, watchlist and open ideas. Dates come from Finnhub every morning and can move.")
            }
            if let problem {
                Text(problem).foregroundStyle(.loss)
            }
            if let calendar {
                if calendar.earnings.isEmpty {
                    ContentUnavailableView("No reports coming", systemImage: "calendar",
                                           description: Text("Watch a stock or take an idea, and its report dates show here."))
                }
                ForEach(calendar.days, id: \.day) { group in
                    Section(Self.title(group.day)) {
                        ForEach(group.reports) { r in
                            NavigationLink { TickerView(ticker: r.ticker) } label: { EarningsCalendarRow(report: r) }
                        }
                    }
                }
            } else if problem == nil {
                ProgressView().frame(maxWidth: .infinity)
            }
        }
        .navigationTitle("Earnings Calendar")
        .task(id: days) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do {
            calendar = try await session.client.earningsCalendar(days: days)
            problem = nil
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    /// "Today", "Tomorrow", or the weekday and date.
    static func title(_ day: String) -> String {
        let today = Stamp.localDay(Date())
        if day == today { return "Today" }
        if let d = Stamp.tradingDay(day) {
            if Calendar.current.isDateInTomorrow(d) { return "Tomorrow" }
            if Calendar.current.isDateInYesterday(d) { return "Yesterday" }
            return d.formatted(.dateTime.weekday(.wide).month(.abbreviated).day())
        }
        return day
    }
}

struct EarningsCalendarRow: View {
    let report: CalendarEarning

    var body: some View {
        let past = report.at < Stamp.localDay(Date())
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(report.ticker).font(.headline)
                if let name = report.name { Text(name).font(.subheadline).foregroundStyle(.secondary).lineLimit(1) }
                Spacer()
                ForEach(report.why, id: \.self) { w in
                    Text(Self.tag(w)).font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Color.ghost.opacity(0.14), in: .capsule).foregroundStyle(Color.ghost)
                }
            }
            if let hour = ReportHour.words(report.hour) {
                Text(hour).font(.caption).foregroundStyle(.secondary)
            }
            if past {
                HStack(spacing: 12) {
                    Text("EPS \(report.actual ?? "—") vs \(report.estimate ?? "—")").monospacedDigit()
                    if let s = report.surprisePct {
                        Text(Self.isBeat(s) ? "Beat \(Show.signedPercent(s))" : "Missed \(Show.signedPercent(s))")
                            .foregroundStyle(Show.tint(s))
                    }
                    if let m = report.movePct { Text("Moved \(Show.signedPercent(m))").foregroundStyle(Show.tint(m)) }
                }
                .font(.subheadline)
            } else {
                HStack(spacing: 12) {
                    Text("EPS estimate \(report.estimate.map(Show.plainNumber) ?? "—")").monospacedDigit()
                    if let t = report.typicalMovePct { Text("Usually moves ±\(Show.percent(t))") }
                }
                .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    static func tag(_ why: String) -> String {
        switch why {
        case "held": "Held"
        case "watched": "Watching"
        case "idea": "Idea"
        default: why.capitalized
        }
    }

    static func isBeat(_ s: String) -> Bool { (Decimal(string: s) ?? 0) >= 0 }
}

/// Analysts' views on the stock page: the consensus word, how the grades split, and the price targets against today's
/// price. Finnhub's monthly trend counts when FMP has no consensus.
struct AnalystLines: View {
    let views: AnalystViews
    let price: String?

    var body: some View {
        if let c = views.consensus, let word = c.consensus {
            HStack(spacing: 10) {
                Text(word.uppercased()).font(.caption.weight(.bold))
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Self.tint(word).opacity(0.15), in: .capsule).foregroundStyle(Self.tint(word))
                if let n = Self.count(c) { Text("\(n) analysts").font(.subheadline).foregroundStyle(.secondary) }
            }
        }
        if let split = Self.split(views) {
            GradeBar(split: split)
            HStack {
                Text("Buy \(split[0] + split[1])").foregroundStyle(Color.gain)
                Spacer()
                Text("Hold \(split[2])").foregroundStyle(.orange)
                Spacer()
                Text("Sell \(split[3] + split[4])").foregroundStyle(Color.loss)
            }
            .font(.caption.weight(.semibold))
        }
        if let c = views.consensus, c.targetConsensus != nil {
            HStack(alignment: .top) {
                Figure(label: "Target low", value: Show.money(c.targetLow))
                Figure(label: "Consensus", value: Show.money(c.targetConsensus))
                Figure(label: "Target high", value: Show.money(c.targetHigh))
            }
            if let upside = Self.upside(target: c.targetConsensus, price: price) {
                Text("Consensus target is \(Show.signedPercent(upside)) from the last close")
                    .font(.caption).foregroundStyle(Show.tint(upside))
            }
        }
        Text(Self.footnote(views)).font(.caption2).foregroundStyle(.tertiary)
    }

    /// Strong buy, buy, hold, sell, strong sell: FMP's consensus counts, else Finnhub's latest month.
    static func split(_ v: AnalystViews) -> [Int]? {
        if let c = v.consensus, let sb = c.strongBuy, let b = c.buy, let h = c.hold, let s = c.sell, let ss = c.strongSell,
           sb + b + h + s + ss > 0 {
            return [sb, b, h, s, ss]
        }
        if let t = v.trends.first, t.total > 0 { return [t.strongBuy, t.buy, t.hold, t.sell, t.strongSell] }
        return nil
    }

    static func count(_ c: AnalystConsensus) -> Int? {
        let n = [c.strongBuy, c.buy, c.hold, c.sell, c.strongSell].compactMap { $0 }.reduce(0, +)
        return n > 0 ? n : nil
    }

    static func upside(target: String?, price: String?) -> String? {
        guard let target, let price, let t = Decimal(string: target), let p = Decimal(string: price), p != 0 else { return nil }
        var pct = (t / p - 1) * 100, rounded = Decimal()
        NSDecimalRound(&rounded, &pct, 2, .plain)
        return NSDecimalNumber(decimal: rounded).stringValue
    }

    static func tint(_ word: String) -> Color {
        let w = word.lowercased()
        if w.contains("buy") { return .gain }
        if w.contains("sell") { return .loss }
        return .orange
    }

    static func footnote(_ v: AnalystViews) -> String {
        var parts: [String] = []
        if let c = v.consensus { parts.append("FMP · \(Stamp.short(c.asOf))") }
        if let t = v.trends.first { parts.append("Finnhub · \(t.period.prefix(7))") }
        return parts.joined(separator: " · ") + " · opinions, not Ghostline's call"
    }
}

/// The grades as one bar: strong buy and buy green, hold orange, sell and strong sell red.
struct GradeBar: View {
    let split: [Int]

    var body: some View {
        let total = max(split.reduce(0, +), 1)
        let parts: [(Int, Color)] = [(split[0] + split[1], .gain), (split[2], .orange), (split[3] + split[4], .loss)]
        GeometryReader { geo in
            HStack(spacing: 2) {
                ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                    if part.0 > 0 {
                        Rectangle().fill(part.1).frame(width: geo.size.width * CGFloat(part.0) / CGFloat(total))
                    }
                }
            }
            .clipShape(.capsule)
        }
        .frame(height: 8)
        .accessibilityHidden(true)
    }
}
