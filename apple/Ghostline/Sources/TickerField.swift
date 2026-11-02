import GhostlineKit
import SwiftUI

/// Search as you type, shared by every place a ticker is typed: the Search tab and the ticker fields. It asks once the
/// owner pauses (250 ms), and an older answer never replaces a newer one.
@MainActor @Observable
final class TickerSearch {
    private(set) var results: [TickerMatch] = []
    private(set) var problem: String?

    func run(_ text: String, client: APIClient, limit: Int = 20) async {
        let q = text.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else {
            results = []
            problem = nil
            return
        }
        try? await Task.sleep(for: .milliseconds(250))
        guard !Task.isCancelled else { return }
        do {
            let found = try await client.searchTickers(q, limit: limit)
            guard !Task.isCancelled else { return }
            results = found
            problem = nil
        } catch {
            guard !Task.isCancelled else { return }
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    func clear() {
        results = []
    }
}

/// A ticker field with suggestions: typing "n" lists Nvidia, Netflix, Nike...; typing part of a name finds the stock.
/// A tap fills the field with the ticker. Typing a ticker the list lacks still works: the field keeps what was typed.
struct TickerField: View {
    @Environment(SessionModel.self) private var session
    let title: String
    @Binding var ticker: String
    var limit = 6
    var onPick: (TickerMatch) -> Void = { _ in }

    @State private var search = TickerSearch()
    @State private var picked: String?
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            TextField(title, text: $ticker)
                #if os(iOS)
                .textInputAutocapitalization(.characters)
                #endif
                .autocorrectionDisabled()
                .focused($focused)
            if focused, ticker != picked, !search.results.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(search.results.prefix(limit)) { match in
                        Button { pick(match) } label: { TickerMatchRow(match: match, compact: true) }
                            .buttonStyle(.plain)
                            .padding(.vertical, 6)
                        if match.id != search.results.prefix(limit).last?.id { Divider() }
                    }
                }
                .padding(.horizontal, 10)
                .background(Color.primary.opacity(0.05), in: .rect(cornerRadius: 10))
                .accessibilityLabel("Suggestions")
            }
        }
        .task(id: ticker) {
            guard ticker != picked else { return }
            await search.run(ticker, client: session.client, limit: limit)
        }
    }

    private func pick(_ match: TickerMatch) {
        picked = match.ticker
        ticker = match.ticker
        search.clear()
        focused = false
        onPick(match)
    }
}

/// One stock in a result list: ticker, name, and what marks it (held, watching, ETF).
struct TickerMatchRow: View {
    let match: TickerMatch
    var compact = false

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(match.ticker).font(compact ? .subheadline.weight(.semibold) : .headline)
                    if let badge = match.badge {
                        Text(badge).font(.caption2.weight(.semibold))
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Color.ghost.opacity(0.14), in: .capsule).foregroundStyle(Color.ghost)
                    }
                }
                Text(Self.name(match.name)).font(compact ? .caption : .subheadline).foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            if match.held == true {
                Image(systemName: "briefcase.fill").foregroundStyle(Color.ghost).accessibilityLabel("You hold it")
            } else if match.watched {
                Image(systemName: "star.fill").foregroundStyle(Color.ghost).accessibilityLabel("Watching")
            }
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }

    /// The SEC's names are in capitals ("NVIDIA CORP"); Massive's are already mixed case and stay as they are.
    static func name(_ s: String) -> String {
        s == s.uppercased() ? s.capitalized : s
    }
}
