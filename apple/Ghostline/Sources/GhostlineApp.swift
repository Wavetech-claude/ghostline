import GhostlineKit
import SwiftUI

@main
struct GhostlineApp: App {
    #if os(iOS)
    @UIApplicationDelegateAdaptor(PushDelegate.self) private var pushDelegate
    #else
    @NSApplicationDelegateAdaptor(PushDelegate.self) private var pushDelegate
    #endif
    @State private var session = SessionModel.fromBundle()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .task { pushDelegate.session = session }
        }
        #if os(macOS)
        .defaultSize(width: 980, height: 680)
        #endif
    }
}
