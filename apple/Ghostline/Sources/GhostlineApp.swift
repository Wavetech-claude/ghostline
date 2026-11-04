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
    @State private var practice: PracticeModel
    @State private var lock = AppLock()

    init() {
        #if DEBUG
        let s = UITestFixture.screen == nil ? SessionModel.fromBundle() : UITestFixture.session()
        #else
        let s = SessionModel.fromBundle()
        #endif
        _session = State(initialValue: s)
        _ledger = State(initialValue: LedgerModel(client: s.client))
        _practice = State(initialValue: PracticeModel(client: s.client))
    }

    var body: some Scene {
        WindowGroup {
            root
                .environment(lock)
                .environment(session)
                .environment(ledger)
                .environment(practice)
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
                Divider()
                Button("Practice Order…") { practice.openTicket() }
                    .keyboardShortcut("n", modifiers: [.command, .option])
                    .disabled(!session.isSignedIn)
            }
        }
        #endif
        #if os(macOS)
        Settings {
            NavigationStack { SettingsView() }
                .environment(lock)
                .environment(session)
                .environment(ledger)
                .environment(practice)
                .frame(minWidth: 480, minHeight: 360)
        }
        #endif
    }

    /// The app; in a Debug build run by the UI tests, the one screen they test (UITestFixture). The screen "app" is
    /// the whole app (sign-in check, app lock, tabs) against the fixed answers.
    @ViewBuilder private var root: some View {
        #if DEBUG
        if let screen = UITestFixture.screen, screen != "app" { UITestHost(screen: screen) } else { RootView() }
        #else
        RootView()
        #endif
    }
}
