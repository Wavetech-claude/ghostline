import Foundation
import Testing
@testable import GhostlineKit

@Suite struct ClaudeModelTests {
    @Test func decodesAMemoWithStruckNumbers() throws {
        let json = #"""
        {"ticker":"AAPL","memos":[{"id":3,"idea_id":null,"ticker":"AAPL","call":"wait","size_money":null,"bull":"Worth [n/a] by June.",
          "base":"b","bear":"c","catalysts":null,"invalidation":"i","what_would_change":"w","confidence_low":"50","confidence_high":"70",
          "grounded":false,"struck":["250.00"],"trace_id":"t","job_id":9,"created_at":"2026-10-30T13:00:00+00:00"}],
         "running":{"id":10,"status":"queued","created_at":"2026-10-30T13:01:00+00:00"}}
        """#
        let m = try GhostlineJSON.decoder().decode(Memos.self, from: Data(json.utf8))
        #expect(m.memos[0].call == "wait" && !m.memos[0].grounded && m.memos[0].struck == ["250.00"])
        #expect(m.running?.id == 10 && m.memos[0].sizeMoney == nil)
    }

    @Test func decodesAJobWithItsResultAndTheThread() throws {
        let job = #"{"id":4,"kind":"read_attachment","status":"done","error":null,"result":{"fields":{"ticker":"MSFT","fill":"410.10"}},"created_at":"x","finished_at":"y"}"#
        let j = try GhostlineJSON.decoder().decode(ClaudeJob.self, from: Data(job.utf8))
        #expect(j.isFinished)
        guard case .object(let r)? = j.result, case .object(let f)? = r["fields"], case .string(let t)? = f["ticker"] else {
            Issue.record("the result's fields did not decode")
            return
        }
        #expect(t == "MSFT")
        let thread = #"{"ticker":null,"messages":[{"id":1,"ticker":null,"role":"owner","content":"hi","grounded":null,"struck":null,"created_at":"x"}],"pending_job":2}"#
        let th = try GhostlineJSON.decoder().decode(ChatThread.self, from: Data(thread.utf8))
        #expect(th.messages[0].role == "owner" && th.pendingJob == 2 && th.ticker == nil)
    }

    @Test func briefCanBeAbsent() throws {
        let b = try GhostlineJSON.decoder().decode(BriefAnswer.self, from: Data(#"{"brief":null,"pending_job":null}"#.utf8))
        #expect(b.brief == nil && b.pendingJob == nil)
    }
}

@Suite struct ClaudeClientTests {
    @Test func aDeepDiveIsAPostAndAFollowUpCarriesTheTicker() async throws {
        let (c, host) = fakeClient(answers: ["/deep-dive": (202, #"{"job_id":7,"ticker":"AAPL"}"#),
                                             "/chat": (202, #"{"job_id":8,"ticker":"AAPL"}"#)],
                                   tokens: InMemoryTokenStore(token: "sess"))
        #expect(try await c.deepDive(ticker: "AAPL").jobId == 7)
        #expect(try await c.ask("Add here?", ticker: "AAPL").jobId == 8)
        let sent = FakeServer.seen(host)
        #expect(sent.map(\.httpMethod) == ["POST", "POST"])
    }
}
