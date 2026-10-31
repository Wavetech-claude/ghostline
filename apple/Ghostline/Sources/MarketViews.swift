import GhostlineKit
import SwiftUI

/// Watchlist: any ticker, its latest stored quote with source and time, a tap opens the chart and the news.
struct WatchlistView: View {
    @Environment(SessionModel.self) private var session
    @State private var rows: [WatchRow] = []
    @State private var error: String?
    @State private var loaded = false
    @State private var adding = false
    @State private var newTicker = ""

    var body: some View {
        List {
            ForEach(rows) { row in
                NavigationLink(value: row.ticker) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.ticker).font(.headline)
                            if let note = row.note, !note.isEmpty { Text(note).font(.caption).foregroundStyle(.secondary) }
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(Show.money(row.quote?.shown)).monospacedDigit()
                            if let q = row.quote {
                                Text("\(q.source) · \(Stamp.short(q.ts))\(q.delayed ? " · delayed" : "")")
                                    .font(.caption2).foregroundStyle(.tertiary)
                            } else {
                                Text("no quote yet").font(.caption2).foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
                .swipeActions {
                    Button("Remove", role: .destructive) { Task { await remove(row.ticker) } }
                }
            }
            if rows.isEmpty && loaded {
                ContentUnavailableView("Watch a ticker", systemImage: "eye",
                                       description: Text("Add any ticker, then ask “Should I buy?” when the deep dive arrives."))
            }
            if let error { Text(error).foregroundStyle(.secondary) }
        }
        .navigationTitle("Watchlist")
        .navigationDestination(for: String.self) { TickerView(ticker: $0) }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { adding = true } label: { Label("Add Ticker", systemImage: "plus") }
            }
        }
        .alert("Watch a Ticker", isPresented: $adding) {
            TextField("Ticker", text: $newTicker)
                #if os(iOS)
                .textInputAutocapitalization(.characters)
                #endif
            Button("Add") { Task { await add() } }
            Button("Cancel", role: .cancel) { newTicker = "" }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do {
            rows = try await session.client.watchlist()
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
        loaded = true
    }

    private func add() async {
        let t = newTicker.trimmingCharacters(in: .whitespaces).uppercased()
        newTicker = ""
        guard !t.isEmpty else { return }
        try? await session.client.watch(t)
        await load()
    }

    private func remove(_ t: String) async {
        try? await session.client.unwatch(t)
        await load()
    }
}

/// One ticker: the chart, then its news, earnings and filings, each with its source and time.
struct TickerView: View {
    @Environment(SessionModel.self) private var session
    @Environment(PracticeModel.self) private var practice
    let ticker: String
    @State private var info: NewsAndEarnings?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 10) {
                    NavigationLink { ChartsView(ticker: ticker) } label: {
                        Label("Chart", systemImage: "chart.xyaxis.line")
                    }
                    NavigationLink { OptionsChainView(ticker: ticker) } label: {
                        Label("Options", systemImage: "list.number")
                    }
                    Button { practice.openTicket(ticker: ticker) } label: {
                        Label("Practice Order", systemImage: "cart")
                    }
                }
                .buttonStyle(.bordered)
                HStack(spacing: 10) {
                    NavigationLink { DeepDiveView(ticker: ticker) } label: {
                        Label("Should I Buy?", systemImage: "sparkles")
                    }
                    .buttonStyle(.borderedProminent)
                    NavigationLink { AlertsView(ticker: ticker) } label: {
                        Label("Alerts", systemImage: "bell")
                    }
                    .buttonStyle(.bordered)
                }
                if let info {
                    if !info.earnings.isEmpty {
                        SectionHeader("Earnings")
                        ForEach(info.earnings) { e in
                            HStack {
                                Text(Stamp.day(e.at))
                                Spacer()
                                Text("est \(e.estimate ?? "—") · actual \(e.actual ?? "—")").monospacedDigit()
                            }
                            .font(.callout)
                            if let move = e.movePct {
                                Text("Moved \(Show.signedPercent(move)) from the close before to the close after")
                                    .font(.caption).foregroundStyle(Show.tint(move))
                            }
                            Text("\(e.source) · as of \(Stamp.short(e.asOf))").font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    if !info.news.isEmpty {
                        SectionHeader("News")
                        ForEach(info.news) { n in
                            if let url = URL(string: n.url) {
                                Link(destination: url) { NewsLine(item: n) }
                            }
                        }
                    }
                    if !info.filings.isEmpty {
                        SectionHeader("Filings")
                        ForEach(info.filings) { f in
                            HStack {
                                Text(f.form).font(.callout.weight(.medium))
                                Text(Stamp.day(f.filedAt)).font(.callout)
                                Spacer()
                                if let u = f.url.flatMap(URL.init) { Link("Open", destination: u).font(.callout) }
                            }
                        }
                    }
                    if info.news.isEmpty && info.earnings.isEmpty && info.filings.isEmpty {
                        Text("Nothing stored for \(ticker) yet. The worker pulls news, earnings and filings every morning.")
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ProgressView()
                }
            }
            .padding()
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle(ticker)
        .task { info = try? await session.client.newsAndEarnings(ticker: ticker) }
    }
}

struct SectionHeader: View {
    let title: String
    init(_ title: String) { self.title = title }
    var body: some View { Text(title).font(.headline).padding(.top, 8) }
}


