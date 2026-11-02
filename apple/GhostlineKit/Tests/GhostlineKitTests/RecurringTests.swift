import Foundation
import Testing
@testable import GhostlineKit

@Suite struct RecurringTests {
    @Test func aPlanDecodesWithItsRunsAndSaysItsSchedule() throws {
        let json = #"{"plans":[{"id":3,"book":"practice","ticker":"VOO","dollars":"50.00","every":"two_weeks","weekday":2,"month_day":null,"starts_on":"2026-11-02","status":"active","note":null,"created_at":"x","updated_at":"x","next_run":"2026-11-04","runs":[{"id":1,"plan_id":3,"day":"2026-11-27","scheduled":"2026-11-26","status":"placed","order_id":9,"reason":"scheduled for 2026-11-26, a day the market was closed","at":"x","order_status":"filled","fill_price":"100.1000","quantity":"0.499500"}]}]}"#
        let p = try GhostlineJSON.decoder().decode(RecurringPlans.self, from: Data(json.utf8)).plans[0]
        #expect(p.scheduleText == "Every other Wednesday" && p.isActive && p.nextRun == "2026-11-04")
        #expect(p.runs[0].scheduled == "2026-11-26" && p.runs[0].orderStatus == "filled")
    }

    @Test func schedulesReadAsWords() {
        #expect(RecurringPlan.ordinal(1) == "1st" && RecurringPlan.ordinal(22) == "22nd" && RecurringPlan.ordinal(13) == "13th")
    }

    @Test func theInputSendsOnlyWhatItsFrequencyUses() throws {
        let weekly = RecurringInput(ticker: "VOO", dollars: "50", every: "week", weekday: 1, monthDay: 15)
        let w = try JSONSerialization.jsonObject(with: GhostlineJSON.encoder().encode(weekly)) as? [String: Any]
        #expect(w?["weekday"] as? Int == 1 && w?["month_day"] == nil)
        let monthly = RecurringInput(ticker: "VOO", dollars: "50", every: "month", weekday: 1, monthDay: 15)
        let m = try JSONSerialization.jsonObject(with: GhostlineJSON.encoder().encode(monthly)) as? [String: Any]
        #expect(m?["month_day"] as? Int == 15 && m?["weekday"] == nil)
    }
}
