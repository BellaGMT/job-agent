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

/** The API returned a message without the fields a stored row needs. Permanent: retrying won't fix it. */
export class MalformedMessageError extends Error {
  override readonly name = "MalformedMessageError";
}

// Long enough for any recruiting email; stops a 2 MB newsletter bloating the DB.
const MAX_BODY_CHARS = 50_000;

/**
 * Validate and flatten one message from the Gmail API.
 * Throws if the fields every stored row depends on (id, thread, date) are missing.
 */
export function parseMessage(message: gmail_v1.Schema$Message): ParsedMessage {
  // Number(null) and Number("") are 0, so check the raw string: a missing date
  // must be rejected, not stored as 1970.
  const internalDate =
    typeof message.internalDate === "string" && /^\d+$/.test(message.internalDate) ? Number(message.internalDate) : NaN;
  if (!message.id || !message.threadId || !Number.isFinite(internalDate)) {
    throw new MalformedMessageError(`Gmail message ${message.id ?? "(no id)"} is missing its id, thread id or date.`);
  }
  const headers = message.payload?.headers ?? [];
  const header = (name: string) =>
    headers.find((candidate) => candidate.name?.toLowerCase() === name.toLowerCase())?.value ?? null;

  return {
    gmailId: message.id,
    threadId: message.threadId,
    rfcMessageId: header("Message-ID"),
    inReplyTo: header("In-Reply-To"),
    referencesIds: header("References"),
    internalDate,
    from: header("From"),
    to: header("To"),
    cc: header("Cc"),
    subject: header("Subject"),
    snippet: message.snippet ? decodeEntities(message.snippet) : null,
    bodyText: extractBody(message.payload).slice(0, MAX_BODY_CHARS),
    labelIds: message.labelIds ?? [],
  };
}

export function extractBody(payload: gmail_v1.Schema$MessagePart | undefined): string {
  const plain: string[] = [];
  const html: string[] = [];
  collectTextParts(payload, plain, html);

  const plainText = clean(plain.join("\n\n"));
  const htmlText = clean(htmlToText(html.join("\n")));
  // Many marketing/ATS emails ship a stub text part ("View this email in your
  // browser") next to the real HTML. Prefer plain only when it carries the content.
  if (plainText.length >= 200 || plainText.length >= htmlText.length) return plainText;
  return htmlText;
}

function collectTextParts(part: gmail_v1.Schema$MessagePart | undefined, plain: string[], html: string[]) {
  if (!part) return;
  // A part with a filename is an attachment (a PDF offer letter, a .ics invite).
  // Skipped for now; the email body almost always restates what matters.
  if (part.filename) return;
  const data = part.body?.data;
  if (part.mimeType === "text/plain" && data) plain.push(decodeBase64Url(data));
  else if (part.mimeType === "text/html" && data) html.push(decodeBase64Url(data));
  for (const child of part.parts ?? []) collectTextParts(child, plain, html);
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

const NAMED_ENTITIES: Record<string, string> = {
  nbsp: " ",
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  rsquo: "\u2019",
  lsquo: "\u2018",
  rdquo: "\u201d",
  ldquo: "\u201c",
  ndash: "\u2013",
  mdash: "\u2014",
  hellip: "\u2026",
  bull: "\u2022",
  middot: "\u00b7",
  copy: "\u00a9",
  reg: "\u00ae",
  trade: "\u2122",
};

export function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (entityText, entity: string) => {
    if (entity.startsWith("#")) {
      const hex = entity[1] === "x" || entity[1] === "X";
      const codePoint = hex ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10);
      // Out-of-range code points would make fromCodePoint throw; leave those as written.
      return codePoint >= 0 && codePoint <= 0x10ffff ? String.fromCodePoint(codePoint) : entityText;
    }
    return NAMED_ENTITIES[entity.toLowerCase()] ?? entityText;
  });
}

// Marketing emails pad their preview text with invisible characters, and some
// put the HTML entity names into their plain-text part literally (&shy; &zwnj;).
const PADDING_ENTITIES = /&(?:shy|zwnj|zwj|#173|#x0*ad|#8203|#8204|#8205|#x0*200[bcd]|#847|#x0*34f|#65279|#x0*feff);/gi;
const INVISIBLE_CHARS = /\u200b|\u200c|\u200d|\u00ad|\u034f|\ufeff/g; // zero-width spaces/joiners, soft hyphen, BOM
// Any whitespace except a newline: spaces, tabs, NBSP, figure/thin/em spaces, ideographic space.
const HORIZONTAL_WHITESPACE = /[^\S\n]+/g;

function clean(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(PADDING_ENTITIES, "")
    .replace(INVISIBLE_CHARS, "")
    .replace(HORIZONTAL_WHITESPACE, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
