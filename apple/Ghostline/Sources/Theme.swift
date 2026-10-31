import SwiftUI

/// Ghostline's colours live in the asset catalog, each with a light and a dark value. Contrast was checked on
/// 2026-10-30 against the white and grey list backgrounds and the black and dark-grey cells: every one is at least
/// 4.8 to 1.
/// - AccentColor is ghost blue, #4F46E5 light and #8586FF dark. The dark value is the lightest that still carries a
///   white button label at 3 to 1 (bold text) while reading at 4.5 to 1 or more on every dark background.
/// - GhostGain and GhostLoss are a deep green and a deep red. Apple's default green reads 2.2 to 1 on white, its red
///   3.5 to 1.
/// Gain and loss always travel with a sign, never as colour alone.
extension ShapeStyle where Self == Color {     // so both Color.gain and .foregroundStyle(.gain) read the same
    /// The brand colour itself, for Ghostline's own lines. Controls take the tint, which on a Mac follows the
    /// system accent the person chose; a chart line stays ghost blue everywhere.
    static var ghost: Color { Color("AccentColor") }
    static var gain: Color { Color("GhostGain") }
    static var loss: Color { Color("GhostLoss") }
}

extension Show {
    /// Gain green, loss red, zero or unknown in the primary colour.
    static func tint(_ s: String?) -> Color {
        switch isGain(s) {
        case .some(true): .gain
        case .some(false): .loss
        case .none: .primary
        }
    }

    static func signedPercent(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "—" }
        return (d > 0 ? "+" : "") + "\(d.formatted(.number.precision(.fractionLength(0...2))))%"
    }
}

extension Animation {
    /// Motion only where it means something: a number rolls to its new value, a chart eases to new data. With Reduce
    /// Motion on, nothing moves.
    static func ghostline(_ reduceMotion: Bool) -> Animation? {
        reduceMotion ? nil : .snappy(duration: 0.35)
    }
}
