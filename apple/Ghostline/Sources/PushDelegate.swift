import Foundation
import GhostlineKit
import UserNotifications

#if os(iOS)
import UIKit

final class PushDelegate: NSObject, UIApplicationDelegate {
    @MainActor var session: SessionModel?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        Task { await PushDelegate.requestPermission { UIApplication.shared.registerForRemoteNotifications() } }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in await session?.pushTokenReceived(token) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {
        // Not an error the owner can act on at launch; the token is sent when it arrives (simulators never get one).
    }
}
#else
import AppKit

final class PushDelegate: NSObject, NSApplicationDelegate {
    @MainActor var session: SessionModel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Task { await PushDelegate.requestPermission { NSApplication.shared.registerForRemoteNotifications() } }
    }

    func application(_ application: NSApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in await session?.pushTokenReceived(token) }
    }

    func application(_ application: NSApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {}
}
#endif

extension PushDelegate {
    @MainActor
    static func requestPermission(then register: @MainActor @escaping () -> Void) async {
        let center = UNUserNotificationCenter.current()
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        if granted { register() }
    }
}
