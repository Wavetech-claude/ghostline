import GhostlineKit
import SwiftUI

/// Search any stock by ticker or company name, the way Robinhood does; with nothing typed, Discover (movers, sectors,
/// industries, themes). Search: a result opens its preview (the owner, 2026-11-02), with Open Full Page, Watch and
/// Practice Buy. A ticker the directory does not list (a fund such as SPY) still opens its page.
struct SearchView: View {
    @Environment(SessionModel.self) private var session
    @Environment(PracticeModel.self) private var practice
    @State private var query = ""
    @State private var search = TickerSearch()
    @State private var previewing: TickerMatch?
    @State private var next: AfterPreview?
    @State private var opened: String?

    /// What a preview's button asked for, done once the preview has closed: the full page is pushed here, and the
    /// practice ticket is a sheet of the main window, which cannot open while the preview is up.
    private enum AfterPreview { case open(String), practice(String) }

    private var symbol: String? {
        let q = query.trimmingCharacters(in: .whitespaces).uppercased()
        return q.range(of: #"^[A-Z][A-Z0-9.\-]{0,9}$"#, options: .regularExpression) != nil ? q : nil
    }

    var body: some View {
        List {
            if query.trimmingCharacters(in: .whitespaces).isEmpty {
                DiscoverSections()
            }
            if let problem = search.problem {
                Text(problem).font(.footnote).foregroundStyle(.secondary)
            }
            if let symbol, !search.results.contains(where: { $0.ticker == symbol }) {
                NavigationLink(value: symbol) { Label("Open \(symbol)", systemImage: "arrow.up.right.square") }
            }
            ForEach(search.results) { r in
                Button { previewing = r } label: { TickerMatchRow(match: r) }
                    .buttonStyle(.plain)
            }
        }
        .navigationTitle("Search")
        .navigationDestination(for: String.self) { TickerView(ticker: $0) }
        .navigationDestination(item: $opened) { TickerView(ticker: $0) }
        .searchable(text: $query, prompt: "Ticker or company")
        .task(id: query) { await search.run(query, client: session.client) }
        .sheet(item: $previewing, onDismiss: afterPreview) { match in
            TickerPreview(match: match, detents: [.fraction(0.62), .large]) { _, watched in
                Button {
                    next = .open(match.ticker)
                    previewing = nil
                } label: {
                    Text("Open Full Page").frame(maxWidth: .infinity)
                }
                .buttonStyle(.glass)
                HStack(spacing: 12) {
                    WatchButton(ticker: match.ticker, watched: watched)
                    Button {
                        next = .practice(match.ticker)
                        previewing = nil
                    } label: {
                        Text("Practice Buy").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.glassProminent)
                }
            }
        }
    }

    private func afterPreview() {
        guard let asked = next else { return }
        next = nil
        switch asked {
        case .open(let ticker): opened = ticker
        case .practice(let ticker): practice.openTicket(ticker: ticker)
        }
    }
}

/// Watch or stop watching a stock, in place.
struct WatchButton: View {
    @Environment(SessionModel.self) private var session
    let ticker: String
    @Binding var watched: Bool
    @State private var busy = false
    @State private var problem: String?

    var body: some View {
        Button { Task { await toggle() } } label: {
            Label(watched ? "Watching" : "Watch", systemImage: watched ? "star.fill" : "star")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.glass)
        .disabled(busy)
        .alert("The watchlist did not change", isPresented: Binding(get: { problem != nil }, set: { if !$0 { problem = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(problem ?? "")
        }
    }

    private func toggle() async {
        busy = true
        defer { busy = false }
        do {
            if watched { try await session.client.unwatch(ticker) } else { try await session.client.watch(ticker) }
            watched.toggle()
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}
