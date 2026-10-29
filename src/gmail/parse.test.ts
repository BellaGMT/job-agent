import { test } from "node:test";
import assert from "node:assert/strict";
import { parseMessage, htmlToText, decodeEntities } from "./parse.js";

const b64 = (s: string) => Buffer.from(s, "utf8").toString("base64url");

test("prefers a real text/plain part and reads headers case-insensitively", () => {
  const msg = parseMessage({
    id: "g1",
    threadId: "t1",
    internalDate: "1761600000000",
    snippet: "Thanks for applying &amp; good luck",
    labelIds: ["INBOX"],
    payload: {
      mimeType: "multipart/alternative",
      headers: [
        { name: "From", value: "Acme Talent <no-reply@myworkday.com>" },
        { name: "subject", value: "Application received: Software Engineer II" },
        { name: "Message-Id", value: "<abc@workday>" },
      ],
      parts: [
        { mimeType: "text/plain", body: { data: b64("Hi Milca,\n\n" + "Thanks for applying. ".repeat(15)) } },
        { mimeType: "text/html", body: { data: b64("<p>html version</p>") } },
      ],
    },
  });
  assert.equal(msg.subject, "Application received: Software Engineer II");
  assert.equal(msg.rfcMessageId, "<abc@workday>");
  assert.equal(msg.internalDate, 1761600000000);
  assert.equal(msg.snippet, "Thanks for applying & good luck");
  assert.match(msg.bodyText, /^Hi Milca,\n\nThanks for applying/);
});

test("falls back to HTML when the plain part is just a stub", () => {
  const html =
    "<html><head><style>p{color:red}</style></head><body>" +
    "<p>Your <b>CodeSignal</b> assessment for Backend Engineer is ready.</p>" +
    "<p>Deadline:&nbsp;November&nbsp;2</p><ul><li>90 minutes</li><li>3 questions</li></ul>" +
    "</body></html>";
  const msg = parseMessage({
    id: "g2",
    threadId: "t2",
    internalDate: "1",
    payload: {
      mimeType: "multipart/mixed",
      headers: [],
      parts: [
        {
          mimeType: "multipart/alternative",
          parts: [
            { mimeType: "text/plain", body: { data: b64("View in browser") } },
            { mimeType: "text/html", body: { data: b64(html) } },
          ],
        },
        // Attachments must be ignored even if they're text.
        { mimeType: "text/plain", filename: "notes.txt", body: { data: b64("SECRET ATTACHMENT") } },
      ],
    },
  });
  assert.equal(
    msg.bodyText,
    "Your CodeSignal assessment for Backend Engineer is ready.\nDeadline: November 2\n\n- 90 minutes\n- 3 questions",
  );
  assert.ok(!msg.bodyText.includes("SECRET"));
  assert.ok(!msg.bodyText.includes("color:red"));
});

test("single-part message with body directly on the payload", () => {
  const msg = parseMessage({
    id: "g3",
    threadId: "t3",
    internalDate: "2",
    payload: { mimeType: "text/plain", headers: [], body: { data: b64("Short note") } },
  });
  assert.equal(msg.bodyText, "Short note");
  assert.equal(msg.from, null);
});

test("entity decoding handles numeric, hex and unknown entities", () => {
  assert.equal(decodeEntities("&#39;hi&#x2019; &bogus; &AMP;"), "'hi’ &bogus; &");
  assert.equal(htmlToText("a<br/>b<!-- x --><td>c</td>"), "a\nb c");
});
