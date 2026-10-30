import AuthenticationServices
import GhostlineKit
import SwiftUI

/// One screen, one action. The line is the mark; the button is the only control.
struct SignInView: View {
    @Environment(SessionModel.self) private var session
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            NightField()
            VStack(spacing: 28) {
                Spacer()
                GhostLineMark()
                    .frame(width: 220, height: 2)
                    .accessibilityHidden(true)
                VStack(spacing: 8) {
                    Text("Ghostline")
                        .font(.system(.largeTitle, design: .default, weight: .semibold))
                        .foregroundStyle(.white)
                    Text("Your own research, one ledger, hard rules.")
                        .font(.callout)
                        .foregroundStyle(.white.opacity(0.7))
                }
                Spacer()
                VStack(spacing: 14) {
                    SignInWithAppleButton(.signIn) { request in
                        request.requestedScopes = [.email]
                    } onCompletion: { result in
                        Task { await session.signIn(with: result) }
                    }
                    .signInWithAppleButtonStyle(.white)
                    .frame(height: 50)
                    .frame(maxWidth: 360)
                    .accessibilityLabel("Sign in with Apple")
                    if let error = session.lastError {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.white.opacity(0.85))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                    }
                    BackendLine()
                        .foregroundStyle(.white.opacity(0.6))
                }
                .padding(.bottom, 40)
            }
            .padding(.horizontal, 32)
        }
        .preferredColorScheme(.dark)
    }
}

/// The night field behind the sign-in: a slow gradient, nothing moving.
struct NightField: View {
    var body: some View {
        LinearGradient(colors: [Color(red: 0.043, green: 0.059, blue: 0.102), Color(red: 0.078, green: 0.106, blue: 0.180)],
                       startPoint: .top, endPoint: .bottom)
        .ignoresSafeArea()
    }
}

/// The mark: a single line that fades at both ends.
struct GhostLineMark: View {
    var body: some View {
        LinearGradient(colors: [.clear, Color(red: 0.839, green: 0.886, blue: 1), .clear], startPoint: .leading, endPoint: .trailing)
            .shadow(color: Color(red: 0.839, green: 0.886, blue: 1).opacity(0.6), radius: 10)
    }
}
