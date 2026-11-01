import GhostlineKit
import SwiftUI

/// Search any stock by ticker or company name, the way Robinhood does: a result opens its stock page, watched or not,
/// and the page has its own Watch button. A ticker the directory does not list (a fund such as SPY) still opens.
struct SearchView: View {
    @Environment(SessionModel.self) private var session
    @State private var query = ""
    @State private var results: [TickerMatch] = []
    @State private var problem: String?

    private var symbol: String? {
        let q = query.trimmingCharacters(in: .whitespaces).uppercased()
        return q.range(of: #"^[A-Z][A-Z0-9.\-]{0,9}$"#, options: .regularExpression) != nil ? q : nil
    }

    var body: some View {
        List {
            if let problem {
                Text(problem).font(.footnote).foregroundStyle(.secondary)
            }
            if let symbol, !results.contains(where: { $0.ticker == symbol }) {
                NavigationLink(value: symbol) { Label("Open \(symbol)", systemImage: "arrow.up.right.square") }
            }
            ForEach(results) { r in
                NavigationLink(value: r.ticker) { row(r) }
            }
        }
        .overlay {
            if query.trimmingCharacters(in: .whitespaces).isEmpty {
                ContentUnavailableView("Search Stocks", systemImage: "magnifyingglass",
                                       description: Text("Type a ticker or a company name."))
            }
        }
        .navigationTitle("Search")
        .navigationDestination(for: String.self) { TickerView(ticker: $0) }
        .searchable(text: $query, prompt: "Ticker or company")
        .task(id: query) { await search() }
    }

    private func row(_ r: TickerMatch) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(r.ticker).font(.headline)
                Text(r.name.capitalized).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if r.watched {
                Image(systemName: "star.fill").foregroundStyle(Color.ghost).accessibilityLabel("Watching")
            }
        }
    }

    private func search() async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else {
            results = []
            problem = nil
            return
        }
        try? await Task.sleep(for: .milliseconds(250))     // typing: ask once the owner pauses
        guard !Task.isCancelled else { return }
        do {
            results = try await session.client.searchTickers(q)
            problem = nil
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}
