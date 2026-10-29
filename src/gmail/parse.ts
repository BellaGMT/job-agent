// Turn a raw Gmail API message (MIME tree, base64url bodies) into flat fields.
// Pure functions, no I/O: easy to unit test, and the same logic will run on
// every email the agent ever sees, so it's worth getting exactly right.
import type { gmail_v1 } from "googleapis";

export interface ParsedMessage {
  gmailId: string;
  threadId: string;
  rfcMessageId: string | null;
  inReplyTo: string | null;
  referencesIds: string | null;
  internalDate: number;
  from: string | null;
  to: string | null;
  cc: string | null;
  subject: string | null;
  snippet: string | null;
  bodyText: string;
  labelIds: string[];
}

// Long enough for any recruiting email; stops a 2 MB newsletter bloating the DB.
const MAX_BODY_CHARS = 50_000;

export function parseMessage(msg: gmail_v1.Schema$Message): ParsedMessage {
  const headers = msg.payload?.headers ?? [];
  const header = (name: string) =>
    headers.find((h) => h.name?.toLowerCase() === name.toLowerCase())?.value ?? null;

  return {
    gmailId: msg.id!,
    threadId: msg.threadId!,
    rfcMessageId: header("Message-ID"),
    inReplyTo: header("In-Reply-To"),
    referencesIds: header("References"),
    internalDate: Number(msg.internalDate),
    from: header("From"),
    to: header("To"),
    cc: header("Cc"),
    subject: header("Subject"),
    snippet: msg.snippet ? decodeEntities(msg.snippet) : null,
    bodyText: extractBody(msg.payload).slice(0, MAX_BODY_CHARS),
    labelIds: msg.labelIds ?? [],
  };
}

export function extractBody(payload: gmail_v1.Schema$MessagePart | undefined): string {
  const plain: string[] = [];
  const html: string[] = [];
  walk(payload, plain, html);

  const plainText = clean(plain.join("\n\n"));
  const htmlText = clean(htmlToText(html.join("\n")));
  // Many marketing/ATS emails ship a stub text part ("View this email in your
  // browser") next to the real HTML. Prefer plain only when it carries the content.
  if (plainText.length >= 200 || plainText.length >= htmlText.length) return plainText;
  return htmlText;
}

function walk(part: gmail_v1.Schema$MessagePart | undefined, plain: string[], html: string[]) {
  if (!part) return;
  // A part with a filename is an attachment (a PDF offer letter, a .ics invite).
  // Skipped for now; the email body almost always restates what matters.
  if (part.filename) return;
  const data = part.body?.data;
  if (part.mimeType === "text/plain" && data) plain.push(decodeBase64Url(data));
  else if (part.mimeType === "text/html" && data) html.push(decodeBase64Url(data));
  for (const child of part.parts ?? []) walk(child, plain, html);
}

// Gmail uses base64url (- and _ instead of + and /). Assumes UTF-8, which
// covers nearly all modern mail; the charset param is ignored for now.
export const decodeBase64Url = (data: string) => Buffer.from(data, "base64url").toString("utf8");

export function htmlToText(html: string): string {
  return decodeEntities(
    html
      .replace(/<(head|style|script)[\s\S]*?<\/\1>/gi, "")
      .replace(/<!--[\s\S]*?-->/g, "")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<li[^>]*>/gi, "\n- ")
      .replace(/<\/(p|div|tr|ul|ol|h[1-6]|table|blockquote)>/gi, "\n")
      .replace(/<(td|th)[^>]*>/gi, " ")
      .replace(/<[^>]+>/g, ""),
  );
}

const NAMED: Record<string, string> = { nbsp: " ", amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

export function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e: string) => {
    if (e[0] === "#") {
      const n = e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(n) ? String.fromCodePoint(n) : m;
    }
    return NAMED[e.toLowerCase()] ?? m;
  });
}

function clean(s: string): string {
  return s
    .replace(/[​‌‍­͏﻿]/g, "") // invisible preheader padding
    .replace(/\r\n/g, "\n")
    .replace(/[ \t ]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
