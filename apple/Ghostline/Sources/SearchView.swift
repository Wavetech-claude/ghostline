import GhostlineKit
import SwiftUI

/// Search any stock by ticker or company name, the way Robinhood does; with nothing typed, Discover (movers, sectors,
/// industries, themes). Search: a result opens its stock page, watched or not,
/// and the page has its own Watch button. A ticker the directory does not list (a fund such as SPY) still opens.
struct SearchView: View {
    @Environment(SessionModel.self) private var session
    @State private var query = ""
    @State private var search = TickerSearch()

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
                NavigationLink(value: r.ticker) { TickerMatchRow(match: r) }
            }
        }
        .navigationTitle("Search")
        .navigationDestination(for: String.self) { TickerView(ticker: $0) }
        .searchable(text: $query, prompt: "Ticker or company")
        .task(id: query) { await search.run(query, client: session.client) }
    }
}
