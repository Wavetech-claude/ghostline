import SwiftUI

/// How to use Ghostline, inside the app (the owner asked for help using it, 2026-10-30). Every time and rule below is
/// read from the worker's schedule and the code; change them together.
struct GuideView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                GhostCard(title: "What Ghostline Does") {
                    Text("Ghostline finds stock ideas in real market data, explains them, and lets you practise before you use real money. It never places a real trade: you place real trades at your broker, then log them here.")
                }
                GhostCard(title: "Set Up (10 Minutes)") {
                    GuideSteps(steps: [
                        "Account → Settings → Risk Rules: set every rule and save. Every idea and every practice order passes through them, and until they are saved no idea passes.",
                        "Account → Add Money → Practice: any amount of practice money.",
                        "Watchlist: add the stocks you follow. News, quotes and company numbers are pulled for them every morning.",
                        "Portfolio → History → Log a Trade: enter what you really hold, so your real balance and day P&L are right.",
                    ])
                }
                GhostCard(title: "A Trading Day, Pacific Time") {
                    GuideLines(lines: [
                        "05:00–06:10: prices, rates, news and earnings are pulled.",
                        "05:30: the morning brief arrives as a notification.",
                        "06:20: the scan makes the day's ideas.",
                        "06:35: approved \"Invest This\" plans are placed in practice.",
                        "06:40: the AI trader buys from the ideas. 06:45: options practice sells puts.",
                        "Every 30 minutes until 13:00: prices, practice fills, price alerts, AI exits.",
                        "13:05: the session closes. 13:15: paper trades are settled and the app learns from them.",
                        "Weekends and market holidays: no market jobs. Sunday: the weekly test and digest.",
                    ])
                }
                GhostCard(title: "The Five Tabs") {
                    GuideLines(lines: [
                        "Today: the brief, what to place at your broker, and Invest an Amount.",
                        "Ideas: today's ideas in three lanes: Long Term, Swings and Sell Puts. Each says why, its entry, stop and how much it risks.",
                        "Watchlist: your stocks. Tap one for its page: the price, a line you drag to read any day, Ghostline's call and the news.",
                        "Portfolio: each book's balance over time, its holdings, and how it did against the S&P 500.",
                        "Account: practice, the AI trader, options practice, your to-dos, price alerts, statements, the journal and settings.",
                    ])
                }
                GhostCard(title: "Ideas and Your Rules") {
                    GuideLines(lines: [
                        "An idea that breaks one of your rules shows as refused, with the rule that refused it.",
                        "Every idea is also traded on paper, and settled after its horizon against the S&P 500, after costs.",
                        "Live: the idea's strategy has proved itself on paper, with 30 settled trades and a positive result against the S&P 500.",
                        "The AI trader's trades can be copied with real money only once their lane has proved itself the same way.",
                    ])
                }
                GhostCard(title: "Practice and Real Money") {
                    GuideLines(lines: [
                        "Practice, the AI trader and options practice fill at the real bid and ask from stored prices, and a sale's cash settles on the next trading day, as in a cash account.",
                        "Real money: \"Invest This\" into Real, or Copy from the AI trader, puts the order on To Place at Your Broker. Place it at your broker, then tap I Placed It: the trade form opens filled in.",
                    ])
                }
                GhostCard(title: "Ask Ghostline") {
                    Text("On a stock's page, Ask Ghostline writes a memo in about a minute: a call (buy, wait or avoid), the bull, base and bear cases, what would prove it wrong, and a confidence range. Its numbers come from the stored data.")
                }
                GhostCard(title: "The AI Trader and Its Ghost") {
                    Text("Give the AI trader any amount. Each trading day after the scan, it trades the open ideas through your rules. Its ghost puts the same money into the S&P 500 at the same times, so you see whether the AI beats simply buying the index.")
                }
                GhostCard(title: "Reading the Numbers") {
                    GuideLines(lines: [
                        "Prices are delayed: one bar a day, and quotes every 30 minutes while the market is open. Nothing streams live.",
                        "Drag any line to read a day. A gain on Portfolio leaves out money you paid in or took out.",
                        "Volume is IEX volume: one exchange only, far below a stock's whole volume.",
                        "Every price shows where it came from and when.",
                    ])
                }
                GhostCard(title: "Your Data") {
                    Text("Only your Apple ID can sign in, and the app sends no analytics.")
                }
            }
            .padding(.vertical, 12)
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("How to Use Ghostline")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }
}

private struct GuideSteps: View {
    let steps: [String]

    var body: some View {
        ForEach(Array(steps.enumerated()), id: \.offset) { i, step in
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("\(i + 1)").font(.subheadline.weight(.bold)).foregroundStyle(Color.ghost).frame(width: 18)
                Text(step).font(.subheadline)
            }
        }
    }
}

private struct GuideLines: View {
    let lines: [String]

    var body: some View {
        ForEach(lines, id: \.self) { line in
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Circle().fill(Color.ghost).frame(width: 5, height: 5).alignmentGuide(.firstTextBaseline) { $0[.bottom] + 1 }
                Text(line).font(.subheadline)
            }
        }
    }
}

/// The first four steps on Today, each ticked from the data itself; the card leaves once all four are done.
struct GetStartedCard: View {
    @Environment(LedgerModel.self) private var ledger
    let watching: Int

    private var practiceFunded: Bool {
        ledger.portfolio?.books.contains { $0.book == "practice" && (Decimal(string: $0.total) ?? 0) > 0 } ?? false
    }

    private var realLogged: Bool {
        ledger.portfolio?.books.contains { $0.book == "real" && !$0.positions.isEmpty } ?? false
    }

    var allDone: Bool { ledger.rulesComplete && practiceFunded && watching > 0 && realLogged }

    var body: some View {
        if !allDone {
            ContentCard {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Get Started").font(.headline)
                    NavigationLink { RiskRulesView() } label: {
                        StartStep(done: ledger.rulesComplete, title: "Set your risk rules", detail: "No idea passes until they are saved.")
                    }
                    .buttonStyle(.plain)
                    Button { ledger.addMoney(to: "practice") } label: {
                        StartStep(done: practiceFunded, title: "Add practice money", detail: "Any amount; it is not real.")
                    }
                    .buttonStyle(.plain)
                    StartStep(done: watching > 0, title: "Watch a few stocks", detail: "In the Watchlist tab, type a ticker and tap Add.")
                    Button { ledger.showingTradeForm = true } label: {
                        StartStep(done: realLogged, title: "Log what you really hold", detail: "So your real balance is right.")
                    }
                    .buttonStyle(.plain)
                    NavigationLink { GuideView() } label: {
                        Label("How to Use Ghostline", systemImage: "questionmark.circle").font(.subheadline.weight(.semibold))
                    }
                    .padding(.top, 4)
                }
            }
        }
    }
}

private struct StartStep: View {
    let done: Bool
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: done ? "checkmark.circle.fill" : "circle")
                .font(.title3)
                .foregroundStyle(done ? Color.gain : Color.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold)).strikethrough(done, color: .secondary)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
        .accessibilityValue(done ? "Done" : "Not done")
    }
}
