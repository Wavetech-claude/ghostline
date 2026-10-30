import GhostlineKit
import SwiftUI

/// A tab bar on iPhone that becomes a sidebar on Mac and iPad.
struct MainView: View {
    @Environment(SessionModel.self) private var session

    var body: some View {
        TabView {
            Tab("Today", systemImage: "sun.horizon") {
                NavigationStack { TodayView() }
            }
            Tab("Account", systemImage: "person.crop.circle") {
                NavigationStack { AccountView() }
            }
            Tab("Settings", systemImage: "slider.horizontal.3") {
                NavigationStack { SettingsView() }
            }
        }
        .tabViewStyle(.sidebarAdaptable)
    }
}

struct TodayView: View {
    @Environment(SessionModel.self) private var session

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                ContentCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Connected", systemImage: "checkmark.seal")
                            .font(.headline)
                        if let backend = session.backend {
                            Text("Ghostline \(backend.shortDescription) on \(session.configuration.environment).")
                        }
                        Text("The morning brief, your watchlist and your ideas arrive here as the app grows.")
                            .foregroundStyle(.secondary)
                    }
                }
                ContentCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Before anything else", systemImage: "shield.lefthalf.filled")
                            .font(.headline)
                        Text("Set your risk rules in Settings. Until they are saved, no idea passes.")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding()
            .frame(maxWidth: 640, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Today")
        .refreshable { await session.refreshBackend() }
    }
}

struct AccountView: View {
    @Environment(SessionModel.self) private var session

    var body: some View {
        Form {
            if case .signedIn(let account) = session.state {
                Section("Signed in") {
                    LabeledContent("Apple ID", value: account.emailMasked.isEmpty ? "hidden" : account.emailMasked)
                    LabeledContent("Member since", value: Self.day(account.createdAt))
                }
            }
            Section {
                Button("Sign out", role: .destructive) {
                    Task { await session.signOut() }
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Account")
    }

    static func day(_ iso: String) -> String {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let plain = ISO8601DateFormatter()
        guard let date = fractional.date(from: iso) ?? plain.date(from: iso) else { return iso }
        return date.formatted(date: .abbreviated, time: .omitted)
    }
}

struct SettingsView: View {
    @Environment(SessionModel.self) private var session

    var body: some View {
        Form {
            Section("Risk rules") {
                Text("Your rules come in the next release: max loss per idea, cash-secured puts only, no new ideas before earnings, liquidity floors, a cap on open ideas, and day trades in practice only. Until you save them, no idea passes.")
                    .foregroundStyle(.secondary)
            }
            Section("Backend") {
                LabeledContent("Address", value: session.configuration.baseURL.host() ?? "")
                LabeledContent("Environment", value: session.configuration.environment)
                if let backend = session.backend {
                    LabeledContent("Version", value: backend.shortDescription)
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
    }
}

/// A content-layer card: a standard material, never glass (glass is for the controls that float above content).
struct ContentCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(18)
            .background(.regularMaterial, in: .rect(cornerRadius: 20))
    }
}
