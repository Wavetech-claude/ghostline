import Foundation

/// Money is a Decimal everywhere in the app, never a Double; shown with two decimals in US dollars.
public enum Money {
    public static func format(_ amount: Decimal, currency: String = "USD") -> String {
        amount.formatted(.currency(code: currency).precision(.fractionLength(2)))
    }

    /// A share of a balance, as the risk rules are written: "1.5%" from 0.015.
    public static func percent(_ share: Decimal) -> String {
        share.formatted(.percent.precision(.fractionLength(0...2)))
    }
}
