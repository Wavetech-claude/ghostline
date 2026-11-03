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

/// What a tap on a suggestion offers on this screen, Robinhood's way (the owner, 2026-11-02): a preview of the stock
/// with the one action this screen is for. Every ticker field names its action, so a tap never only fills the box.
struct TickerAction {
    var title: (TickerMatch) -> String
    /// The title when the action is already done (the stock is on the watchlist): the button shows it and is off.
    var done: (TickerMatch) -> String? = { _ in nil }
    /// Runs after the preview has closed and the field holds the ticker.
    var run: (TickerChoice) -> Void = { _ in }

    /// "Use NFLX": fills the field, then `run`.
    static func use(_ run: @escaping (TickerChoice) -> Void = { _ in }) -> TickerAction {
        TickerAction(title: { "Use \($0.ticker)" }, run: run)
    }
}

/// The stock chosen in a preview, with the price the preview showed (nil when none is stored yet).
struct TickerChoice: Equatable {
    let match: TickerMatch
    let price: String?
}

/// A ticker field with suggestions: typing "n" lists Nvidia, Netflix, Nike...; typing part of a name finds the stock.
/// A tap opens the stock's preview with this screen's action; the action fills the field. Typing a ticker the list
/// lacks still works: the field keeps what was typed.
struct TickerField: View {
    @Environment(SessionModel.self) private var session
    let title: String
    @Binding var ticker: String
    var limit = 6
    let action: TickerAction

    @State private var search = TickerSearch()
    @State private var picked: String?
    @State private var previewing: TickerMatch?
    @State private var chosen: TickerChoice?
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
                        Button { preview(match) } label: { TickerMatchRow(match: match, compact: true) }
                            .buttonStyle(.plain)
                            .padding(.vertical, 6)
                        if match.id != search.results.prefix(limit).last?.id { Divider() }
                    }
                }
                .padding(.horizontal, 10)
                .background(Color.primary.opacity(0.05), in: .rect(cornerRadius: 10))
                // a container: each suggestion stays its own button (a label alone merged the list into one element)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Suggestions")
                .accessibilityIdentifier("suggestions")
            }
        }
        .task(id: ticker) {
            guard ticker != picked else { return }
            await search.run(ticker, client: session.client, limit: limit)
        }
        .sheet(item: $previewing, onDismiss: finish) { match in
            TickerPreview(match: match) { choice, _ in
                let done = action.done(match)
                Button {
                    chosen = choice
                    previewing = nil
                } label: {
                    Text(done ?? action.title(match)).frame(maxWidth: .infinity)
                }
                .buttonStyle(.glassProminent)
                .disabled(done != nil)
            }
        }
    }

    private func preview(_ match: TickerMatch) {
        focused = false
        previewing = match
    }

    /// After the preview has closed: fill the field, then the screen's action. It runs only once the preview is gone,
    /// so an action that closes the screen's own sheet never races the preview's.
    private func finish() {
        guard let choice = chosen else { return }
        chosen = nil
        picked = choice.match.ticker
        ticker = choice.match.ticker
        search.clear()
        action.run(choice)
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
