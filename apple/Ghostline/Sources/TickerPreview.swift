import GhostlineKit
import SwiftUI

/// What a tap on a search suggestion opens, anywhere in the app. The owner, 2026-11-02: "when you click/tap it, it
/// shows a preview with action button based on where you are on the app", as Robinhood does. The card has:
/// - the ticker and the company;
/// - today's price and change, and the day's line;
/// - whether the owner holds or watches it;
/// - the actions of the screen it was opened from, at the bottom.
struct TickerPreview<Actions: View>: View {
    @Environment(SessionModel.self) private var session
    @Environment(\.dismiss) private var dismiss
    let match: TickerMatch
    /// The sheet's heights: half the screen fits one row of buttons; the Search tab's two rows ask for more.
    var detents: Set<PresentationDetent> = [.medium, .large]
    /// The screen's buttons: they get the choice (the stock and the price shown) and whether it is watched.
    let actions: (TickerChoice, Binding<Bool>) -> Actions

    @State private var chart: ChartData?
    @State private var holdings: [String] = []
    @State private var watched: Bool
    @State private var problem: String?
    @State private var loaded = false

    init(match: TickerMatch, detents: Set<PresentationDetent> = [.medium, .large],
         @ViewBuilder actions: @escaping (TickerChoice, Binding<Bool>) -> Actions) {
        self.match = match
        self.detents = detents
        self.actions = actions
        _watched = State(initialValue: match.watched)
    }

    private var quote: TickerQuote { TickerQuote(chart: chart) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    if let chart, chart.stockPoints.count > 1 {
                        StockLine(points: chart.stockPoints, picked: .constant(nil), label: "Today's price line",
                                  baseline: chart.previousClose.flatMap(Double.init))
                            .frame(height: 110)
                    }
                    marks
                    if let problem { Text(problem).font(.footnote).foregroundStyle(.secondary) }
                }
                .padding(.horizontal, 20)
                .padding(.top, 4)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .safeAreaBar(edge: .bottom) {
                VStack(spacing: 10) { actions(TickerChoice(match: match, price: quote.price), $watched) }
                    .controlSize(.large)
                    .padding(.horizontal, 20)
                    .padding(.bottom, 8)
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close", systemImage: "xmark") { dismiss() }
                }
            }
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
        }
        .presentationDetents(detents)
        .task { await load() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(match.ticker).font(.title3.weight(.bold))
                Text(TickerMatchRow.name(match.name)).foregroundStyle(.secondary).lineLimit(1)
                if let badge = match.badge {
                    Text(badge).font(.caption2.weight(.semibold))
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(Color.ghost.opacity(0.14), in: .capsule).foregroundStyle(Color.ghost)
                }
            }
            if let price = quote.price {
                Text(Show.money(price)).font(.system(size: 34, weight: .semibold, design: .rounded)).monospacedDigit()
                    .accessibilityIdentifier("previewPrice")
                if let money = quote.changeMoney, let percent = quote.changePercent {
                    HStack(spacing: 8) {
                        Text("\(Show.signedMoney(money))  \(Show.signedPercent(percent))").monospacedDigit()
                            .padding(.horizontal, 10).padding(.vertical, 4)
                            .background(Show.tint(money).opacity(0.14), in: .capsule)
                            .foregroundStyle(Show.tint(money))
                        Text(quote.when).foregroundStyle(.secondary)
                    }
                    .font(.subheadline.weight(.semibold))
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("previewChange")
                }
            } else if loaded {
                Text("No price stored yet.").foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
    }

    @ViewBuilder private var marks: some View {
        let lines = holdings.map { "You hold \($0)" } + (watched ? ["Watching"] : [])
        if !lines.isEmpty {
            Label(lines.joined(separator: " · "), systemImage: holdings.isEmpty ? "star.fill" : "briefcase.fill")
                .font(.subheadline)
                .foregroundStyle(Color.ghost)
                .accessibilityIdentifier("previewMarks")
        }
    }

    private func load() async {
        do {
            chart = try await session.client.chart(ticker: match.ticker, timeframe: "1D")
            problem = nil
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
        if match.held == true, let p = try? await session.client.portfolio() {
            holdings = p.books.compactMap { b in
                b.positions.first { $0.ticker == match.ticker }.map { "\(Show.quantity($0.quantity)) in \(b.name)" }
            }
        }
        loaded = true
    }
}

/// The price a preview shows: the 1D chart's latest close, and its change from the previous session's close (as the
/// stock page measures a 1D chart). No price when no bar is stored; no change when the previous close is unknown.
struct TickerQuote: Equatable {
    var price: String?
    var changeMoney: String?
    var changePercent: String?
    var when = ""

    init(chart: ChartData?) {
        guard let chart, let last = chart.bars.last else { return }
        price = last.close
        if let base = chart.previousClose, let d = StockSections.difference(last.close, base) {
            changeMoney = d.money
            changePercent = d.percent
        }
        when = chart.marketOpen == true ? "Today" : "At the close"
    }
}

/// The price the preview showed for the chosen stock, while the field still holds that stock (an alert or an order
/// is set against it).
struct ChosenPriceRow: View {
    let choice: TickerChoice?
    let ticker: String

    var body: some View {
        if let c = choice, let price = c.price,
           c.match.ticker == ticker.trimmingCharacters(in: .whitespaces).uppercased() {
            LabeledContent("\(c.match.ticker) Now", value: Show.money(price))
                .accessibilityIdentifier("chosenPrice")
        }
    }
}
