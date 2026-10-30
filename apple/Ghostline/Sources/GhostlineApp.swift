import GhostlineKit
import SwiftUI

@main
struct GhostlineApp: App {
    #if os(iOS)
    @UIApplicationDelegateAdaptor(PushDelegate.self) private var pushDelegate
    #else
    @NSApplicationDelegateAdaptor(PushDelegate.self) private var pushDelegate
    #endif
    @State private var session: SessionModel
    @State private var ledger: LedgerModel

    init() {
        let s = SessionModel.fromBundle()
        _session = State(initialValue: s)
        _ledger = State(initialValue: LedgerModel(client: s.client))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .environment(ledger)
                .task { pushDelegate.session = session }
        }
        #if os(macOS)
        .defaultSize(width: 980, height: 680)
        .commands {
            CommandMenu("Ledger") {
                Button("Log a Trade…") { ledger.showingTradeForm = true }
                    .keyboardShortcut("n", modifiers: .command)
                    .disabled(!session.isSignedIn)
                Button("Add Money…") { ledger.showingCashFlow = true }
                    .keyboardShortcut("n", modifiers: [.command, .shift])
                    .disabled(!session.isSignedIn)
            }
        }
        #endif
        #if os(macOS)
        Settings {
            NavigationStack { SettingsView() }
                .environment(session)
                .environment(ledger)
                .frame(minWidth: 480, minHeight: 360)
        }
        #endif
    }
}
