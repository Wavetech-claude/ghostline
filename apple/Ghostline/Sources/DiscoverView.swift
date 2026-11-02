import GhostlineKit
import SwiftUI

/// Discover, on the Search tab while nothing is typed: movers (day, week or month; gainers, losers or most active;
/// stocks or ETFs; one industry), sectors, industries and themes. Every stock opens its page.
struct DiscoverSections: View {
    enum Part: String, CaseIterable, Identifiable {
        case movers = "Movers", sectors = "Sectors", industries = "Industries", themes = "Themes"
        var id: String { rawValue }
    }

    @State private var part: Part = .movers

    var body: some View {
        Section {
            Picker("Discover", selection: $part) {
                ForEach(Part.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
        }
        switch part {
        case .movers: MoversSections()
        case .sectors: SectorSections()
        case .industries: IndustrySections()
        case .themes: ThemeSections()
        }
    }
}

/// Day, Week, Month.
struct PeriodPicker: View {
    @Binding var period: String

    var body: some View {
        Picker("Period", selection: $period) {
            Text("Day").tag("day")
            Text("Week").tag("week")
            Text("Month").tag("month")
        }
        .pickerStyle(.segmented)
    }
}

struct MoversSections: View {
    @Environment(SessionModel.self) private var session
    var industry: String? = nil
    @State private var period = "day"
    @State private var direction = "gainers"
    @State private var kind = "all"
    @State private var pickedIndustry: String?
    @State private var underFive = false
    @State private var list: MoversList?
    @State private var names: [IndustryName] = []
    @State private var problem: String?

    private var shownIndustry: String? { industry ?? pickedIndustry }

    var body: some View {
        Section {
            PeriodPicker(period: $period)
            Picker("List", selection: $direction) {
                Text("Gainers").tag("gainers")
                Text("Losers").tag("losers")
                Text("Most Active").tag("active")
            }
            .pickerStyle(.segmented)
            Picker("Show", selection: $kind) {
                Text("Stocks and ETFs").tag("all")
                Text("Stocks").tag("stocks")
                Text("ETFs").tag("etfs")
            }
            if industry == nil {
                Picker("Industry", selection: $pickedIndustry) {
                    Text("All industries").tag(String?.none)
                    ForEach(names) { Text($0.name).tag(String?.some($0.name)) }
                }
            }
            Toggle("Include stocks under $5", isOn: $underFive)
        } footer: {
            Text(footnote)
        }
        Section {
            if let problem { Text(problem).foregroundStyle(.loss) }
            if let list {
                if list.rows.isEmpty {
                    Text("Nothing on this list yet. The market's daily file fills during the first mornings.")
                        .foregroundStyle(.secondary)
                }
                ForEach(list.rows) { r in
                    NavigationLink(value: r.ticker) { MoverLine(row: r, direction: direction) }
                }
            } else if problem == nil {
                ProgressView().frame(maxWidth: .infinity)
            }
        }
        .task(id: "\(period)-\(direction)-\(kind)-\(shownIndustry ?? "")-\(underFive)") { await load() }
        .task { if industry == nil { names = (try? await session.client.industryNames()) ?? [] } }
    }

    private var footnote: String {
        guard let list else { return " " }
        if list.isLive { return "Live from Alpaca, the whole market · \(Stamp.short(list.through))" }
        let span = [list.since, list.through].compactMap { $0.map { Stamp.day($0) } }.joined(separator: " to ")
        return "Closing prices, \(span) · warrants and stocks trading under $1M a day are left out"
    }

    private func load() async {
        do {
            list = try await session.client.movers(period: period, direction: direction, kind: kind,
                                                   industry: shownIndustry, minPrice: underFive ? 0 : 5)
            problem = nil
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct MoverLine: View {
    let row: MoverRow
    var direction = "gainers"

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(row.ticker).font(.headline)
                    if let kind = row.kind, ["ETF", "ETN", "ETV", "ETS", "FUND"].contains(kind) {
                        Text("ETF").font(.caption2.weight(.semibold))
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Color.ghost.opacity(0.14), in: .capsule).foregroundStyle(Color.ghost)
                    }
                }
                Text([row.name.map(TickerMatchRow.name), row.industry].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(Show.money(row.price)).monospacedDigit()
                if direction == "active", let dv = row.dollarVolume ?? row.volume {
                    Text(Self.compact(dv) + (row.dollarVolume != nil ? " traded" : " shares"))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(Show.signedPercent(row.changePct)).font(.subheadline.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(Show.tint(row.changePct))
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    static func compact(_ s: String) -> String {
        (Double(s) ?? 0).formatted(.number.notation(.compactName))
    }
}

struct SectorSections: View {
    @Environment(SessionModel.self) private var session
    @State private var period = "day"
    @State private var list: SectorList?

    var body: some View {
        Section {
            PeriodPicker(period: $period)
        } footer: {
            Text("Each sector through its Select Sector SPDR fund\(list?.through.map { " · through \(Stamp.day($0))" } ?? "").")
        }
        Section {
            ForEach(list?.rows ?? []) { s in
                NavigationLink(value: s.etf) {
                    ChangeLine(title: s.name, detail: s.etf, change: s.changePct)
                }
            }
        }
        .task(id: period) { list = try? await session.client.sectors(period: period) }
    }
}

struct IndustrySections: View {
    @Environment(SessionModel.self) private var session
    @State private var period = "day"
    @State private var list: IndustryList?

    var body: some View {
        Section {
            PeriodPicker(period: $period)
        } footer: {
            Text("The median move of each industry's stocks trading $5M a day or more (\(list?.tagged ?? 0) stocks tagged by Finnhub).")
        }
        Section {
            ForEach(list?.rows ?? []) { i in
                NavigationLink {
                    List { MoversSections(industry: i.name) }.navigationTitle(i.name)
                } label: {
                    ChangeLine(title: i.name, detail: "\(i.stocks) stocks\(i.best.map { " · best \($0)" } ?? "")",
                               change: i.changePct)
                }
            }
        }
        .task(id: period) { list = try? await session.client.industries(period: period) }
    }
}

struct ThemeSections: View {
    @Environment(SessionModel.self) private var session
    @State private var period = "week"
    @State private var list: ThemeList?

    var body: some View {
        Section {
            PeriodPicker(period: $period)
        } footer: {
            Text("Themes chosen by hand: their funds, and the leading stocks of their industries.")
        }
        ForEach(list?.themes ?? []) { t in
            Section {
                ForEach(t.etfs) { f in
                    NavigationLink(value: f.ticker) {
                        ChangeLine(title: f.ticker, detail: f.name.map(TickerMatchRow.name) ?? "Fund", change: f.changePct)
                    }
                }
                ForEach(t.leaders) { r in
                    NavigationLink(value: r.ticker) { MoverLine(row: r) }
                }
            } header: {
                Text(t.name)
            } footer: {
                if let note = t.note { Text(note) }
            }
        }
        .task(id: period) { list = try? await session.client.themes(period: period) }
    }
}

/// A name, a detail line, and the move in its gain or loss colour.
struct ChangeLine: View {
    let title: String
    let detail: String
    let change: String?

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.body)
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Text(Show.signedPercent(change)).font(.subheadline.weight(.semibold)).monospacedDigit()
                .foregroundStyle(Show.tint(change))
        }
        .accessibilityElement(children: .combine)
    }
}
