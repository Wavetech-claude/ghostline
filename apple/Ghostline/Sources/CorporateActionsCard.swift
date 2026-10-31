import GhostlineKit
import SwiftUI

/// On Today: the real book's splits and dividends waiting for the owner. The practice, AI and options books took them
/// on their own; the real book takes one only when the owner confirms the broker shows it.
struct PendingActionsCard: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger
    @State private var actions: [PendingAction] = []
    @State private var editing: PendingAction?
    @State private var paid = ""
    @State private var problem: String?

    var body: some View {
        VStack(spacing: 0) {            // present even when empty, so its task runs
            if !actions.isEmpty {
                ContentCard {
                    VStack(alignment: .leading, spacing: 14) {
                        Label("Check Against Your Broker", systemImage: "arrow.triangle.branch").font(.headline)
                        ForEach(actions) { a in action(a) }
                        if let problem { Text(problem).font(.footnote).foregroundStyle(.loss) }
                    }
                }
            }
        }
        .task { await load() }
        .alert("Amount Your Broker Paid",
               isPresented: Binding(get: { editing != nil }, set: { if !$0 { editing = nil } })) {
            TextField("0.00", text: $paid)
            Button("Confirm") { if let a = editing { Task { await confirm(a, amount: paid) } } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func action(_ a: PendingAction) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(a.text).font(.subheadline.weight(.semibold))
            Text(a.isDividend ? "Paid \(Stamp.day(a.payableDate ?? a.exDate)). Your practice books have it already."
                              : "From \(Stamp.day(a.exDate)). Your practice books have it already.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Confirm") { Task { await confirm(a, amount: nil) } }.buttonStyle(.borderedProminent)
                Button("Not in My Account") { Task { await dismiss(a) } }.buttonStyle(.bordered)
            }
            .controlSize(.small)
            if a.isDividend {
                Button("My broker paid a different amount") {
                    paid = a.amount ?? ""
                    editing = a
                }
                .font(.caption)
            }
        }
    }

    private func load() async {
        actions = (try? await session.client.pendingActions()) ?? []
    }

    private func confirm(_ a: PendingAction, amount: String?) async {
        await act { try await session.client.confirmAction(id: a.id, amount: amount) }
    }

    private func dismiss(_ a: PendingAction) async {
        await act { try await session.client.dismissAction(id: a.id) }
    }

    private func act(_ call: () async throws -> Void) async {
        do {
            try await call()
            problem = nil
            await load()
            await ledger.loadPortfolio()
        } catch {
            problem = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}
