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
